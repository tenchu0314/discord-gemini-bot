import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from google.genai import errors, types
import bot


class TestSplitMessage(unittest.TestCase):
    def test_split_message_empty_and_none(self):
        self.assertEqual(bot.split_message(""), [])
        self.assertEqual(bot.split_message(None), [])

    def test_split_message_within_limit(self):
        text = "こんにちは、世界！"
        self.assertEqual(bot.split_message(text, limit=2000), [text])

    def test_split_message_exact_boundary(self):
        text = "a" * 2000
        self.assertEqual(bot.split_message(text, limit=2000), [text])

    def test_split_message_exceeds_limit_multiple_chunks(self):
        text = "a" * 4500
        chunks = bot.split_message(text, limit=2000)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(len(chunks[0]), 2000)
        self.assertEqual(len(chunks[1]), 2000)
        self.assertEqual(len(chunks[2]), 500)
        self.assertEqual("".join(chunks), text)

    def test_split_message_invalid_limit_raises_value_error(self):
        with self.assertRaises(ValueError):
            bot.split_message("test", limit=0)
        with self.assertRaises(ValueError):
            bot.split_message("test", limit=-10)


class TestRemoveThinking(unittest.TestCase):
    def test_remove_thinking_empty_and_none(self):
        self.assertEqual(bot.remove_thinking(""), "")
        self.assertEqual(bot.remove_thinking(None), "")

    def test_remove_thinking_strips_think_and_thought_tags(self):
        raw = "<think>\n内部の推論プロセス\n</think>\n最終的な回答です。"
        self.assertEqual(bot.remove_thinking(raw), "最終的な回答です。")

        raw_thought = "<thought>英語で考える</thought>日本語の回答"
        self.assertEqual(bot.remove_thinking(raw_thought), "日本語の回答")

    def test_remove_thinking_case_insensitive(self):
        raw = "<THINK>大文字タグ</THINK><Thought>混在タグ</Thought>本番テキスト"
        self.assertEqual(bot.remove_thinking(raw), "本番テキスト")

    def test_remove_thinking_unclosed_truncated_tags(self):
        raw_unclosed_think = "冒頭の回答<think>途中でトークン上限に達して閉じタグがない推論"
        self.assertEqual(bot.remove_thinking(raw_unclosed_think), "冒頭の回答")

        raw_unclosed_thought = "<THOUGHT>最初から最後まで閉じられていない思考"
        self.assertEqual(bot.remove_thinking(raw_unclosed_thought), "")


class TestGetSystemInstruction(unittest.TestCase):
    def test_get_system_instruction_reads_default_soul_file(self):
        instruction = bot.get_system_instruction()
        self.assertEqual(instruction, "日本語回答する")

    def test_get_system_instruction_with_custom_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_soul = Path(tmpdir) / "SOUL.md"
            custom_soul.write_text("  関西弁で回答する\n", encoding="utf-8")
            with patch.object(bot, "SOUL_FILE", custom_soul):
                self.assertEqual(bot.get_system_instruction(), "関西弁で回答する")

            missing_soul = Path(tmpdir) / "NON_EXISTENT_SOUL.md"
            with patch.object(bot, "SOUL_FILE", missing_soul):
                self.assertIsNone(bot.get_system_instruction())


class TestRedactSecrets(unittest.TestCase):
    def test_redact_secrets_masks_tokens_and_keys(self):
        with patch.object(bot, "GOOGLE_API_KEY", "secret-google-key-123"), patch.object(
            bot, "DISCORD_BOT_TOKEN", "secret-discord-token-456"
        ):
            msg = "Failed request with key=secret-google-key-123 and token=secret-discord-token-456"
            redacted = bot.redact_secrets(msg)
            self.assertNotIn("secret-google-key-123", redacted)
            self.assertNotIn("secret-discord-token-456", redacted)
            self.assertIn("[REDACTED]", redacted)

    def test_redact_secrets_masks_url_encoded_and_query_patterns(self):
        with patch.object(bot, "GOOGLE_API_KEY", "key+with/slash=123"):
            msg = (
                "GET https://example.com/v1?key=key%2Bwith%2Fslash%3D123 "
                "and fallback AIzaSyDummyKeyValueForTesting1234567890"
            )
            redacted = bot.redact_secrets(msg)
            self.assertNotIn("key%2Bwith%2Fslash%3D123", redacted)
            self.assertNotIn("AIzaSyDummyKeyValueForTesting1234567890", redacted)
            self.assertIn("[REDACTED]", redacted)


class TestGenerateContent(unittest.TestCase):
    def test_generate_content_passes_google_search_and_system_instruction(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "検索結果に基づく回答"
        mock_client.models.generate_content.return_value = mock_response

        with patch.object(bot, "get_gemini_client", return_value=mock_client), patch.object(
            bot, "get_system_instruction", return_value="日本語回答する"
        ):
            result = bot.generate_content("今日の天気は？")

        self.assertEqual(result, mock_response)
        mock_client.models.generate_content.assert_called_once()
        _, kwargs = mock_client.models.generate_content.call_args
        self.assertEqual(kwargs["model"], bot.GEMINI_MODEL)
        self.assertEqual(kwargs["contents"], "今日の天気は？")
        config = kwargs["config"]
        self.assertEqual(config.system_instruction, "日本語回答する")
        self.assertEqual(len(config.tools), 1)
        self.assertIsNotNone(config.tools[0].google_search)

    def test_generate_content_retries_and_reraises_original_exception(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("Temporary API failure")

        with patch.object(bot, "get_gemini_client", return_value=mock_client), patch.object(
            bot, "get_system_instruction", return_value="日本語回答する"
        ), patch.object(bot.generate_content.retry, "sleep", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                bot.generate_content("テスト質問")

        self.assertIn("Temporary API failure", str(ctx.exception))
        self.assertEqual(mock_client.models.generate_content.call_count, bot.MAX_RETRY_ATTEMPTS)

    def test_generate_content_does_not_retry_permanent_client_errors(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = errors.ClientError(
            403, {"error": {"message": "Forbidden"}}
        )

        with patch.object(bot, "get_gemini_client", return_value=mock_client), patch.object(
            bot, "get_system_instruction", return_value="日本語回答する"
        ), patch.object(bot.generate_content.retry, "sleep", return_value=None):
            with self.assertRaises(errors.ClientError):
                bot.generate_content("権限エラーテスト")

        self.assertEqual(mock_client.models.generate_content.call_count, 1)

    def test_generate_content_retries_rate_limit_and_server_errors(self):
        for exc in (
            errors.ClientError(429, {"error": {"message": "Too Many Requests"}}),
            errors.ServerError(503, {"error": {"message": "Service Unavailable"}}),
        ):
            with self.subTest(code=exc.code):
                mock_client = MagicMock()
                mock_client.models.generate_content.side_effect = exc

                with patch.object(bot, "get_gemini_client", return_value=mock_client), patch.object(
                    bot, "get_system_instruction", return_value="日本語回答する"
                ), patch.object(bot.generate_content.retry, "sleep", return_value=None):
                    with self.assertRaises(errors.APIError):
                        bot.generate_content("一時エラーテスト")

                self.assertEqual(
                    mock_client.models.generate_content.call_count,
                    bot.MAX_RETRY_ATTEMPTS,
                )

    def test_generate_content_falls_back_to_default_model_when_empty(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = SimpleNamespace(text="ok")

        with patch.object(bot, "GEMINI_MODEL", "   "), patch.object(
            bot, "get_gemini_client", return_value=mock_client
        ), patch.object(bot, "get_system_instruction", return_value=None):
            bot.generate_content("テスト")

        _, kwargs = mock_client.models.generate_content.call_args
        self.assertEqual(kwargs["model"], bot.DEFAULT_GEMINI_MODEL)

    def test_get_gemini_client_sets_timeout_http_options(self):
        with patch.object(bot, "gemini_client", None), patch.object(
            bot, "GOOGLE_API_KEY", "test-api-key"
        ), patch.object(bot.genai, "Client") as mock_client_cls:
            bot.get_gemini_client()

        mock_client_cls.assert_called_once()
        _, kwargs = mock_client_cls.call_args
        self.assertEqual(kwargs["api_key"], "test-api-key")
        self.assertIsInstance(kwargs["http_options"], types.HttpOptions)
        self.assertEqual(kwargs["http_options"].timeout, bot.GEMINI_TIMEOUT_MS)

    def test_missing_api_key_error_is_not_retried(self):
        self.assertFalse(bot.is_retryable_error(RuntimeError("GOOGLE_API_KEY is not set.")))
        self.assertFalse(bot.is_retryable_error(ValueError("invalid argument")))


class TestDiscordClientAndOnMessage(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot_user = SimpleNamespace(id=999999999, bot=True)

    def _make_message(self, content, author=None, mentioned=True):
        if author is None:
            author = SimpleNamespace(id=111111111, bot=False)
        typing_cm = MagicMock()
        typing_cm.__aenter__ = AsyncMock(return_value=None)
        typing_cm.__aexit__ = AsyncMock(return_value=None)

        channel = SimpleNamespace(
            send=AsyncMock(),
            typing=MagicMock(return_value=typing_cm),
        )
        message = SimpleNamespace(
            author=author,
            content=content,
            mentions=[self.bot_user] if mentioned else [],
            channel=channel,
            reply=AsyncMock(),
        )
        return message

    def test_client_disables_allowed_mentions(self):
        allowed = bot.client.allowed_mentions
        self.assertIsNotNone(allowed)
        self.assertFalse(allowed.everyone)
        self.assertFalse(allowed.users)
        self.assertFalse(allowed.roles)
        self.assertFalse(allowed.replied_user)

    async def test_on_message_ignores_self_and_other_bots(self):
        with patch.object(bot.client, "_connection") as mock_conn:
            mock_conn.user = self.bot_user

            # ボット自身のメッセージ
            self_msg = self._make_message(f"<@{self.bot_user.id}> こんにちは", author=self.bot_user)
            await bot.on_message(self_msg)
            self_msg.reply.assert_not_called()
            self_msg.channel.send.assert_not_called()

            # 他のボットからのメッセージ
            other_bot = SimpleNamespace(id=222222222, bot=True)
            bot_msg = self._make_message(f"<@{self.bot_user.id}> ループテスト", author=other_bot)
            await bot.on_message(bot_msg)
            bot_msg.reply.assert_not_called()
            bot_msg.channel.send.assert_not_called()

    async def test_on_message_empty_prompt_with_standard_and_nickname_mention(self):
        with patch.object(bot.client, "_connection") as mock_conn:
            mock_conn.user = self.bot_user

            # 通常メンション <@ID> のみ
            msg1 = self._make_message(f"<@{self.bot_user.id}>   ")
            await bot.on_message(msg1)
            msg1.channel.send.assert_awaited_once_with(
                "何か質問を入力してください。（例: `@Botの名前 富士山の高さは？`）"
            )

            # ニックネームメンション <@!ID> のみ
            msg2 = self._make_message(f"<@!{self.bot_user.id}> ")
            await bot.on_message(msg2)
            msg2.channel.send.assert_awaited_once_with(
                "何か質問を入力してください。（例: `@Botの名前 富士山の高さは？`）"
            )

    async def test_on_message_strips_nickname_mention_and_replies(self):
        fake_response = SimpleNamespace(text="<think>推論中</think>富士山は3776mです。")
        with patch.object(bot.client, "_connection") as mock_conn, patch.object(
            bot, "generate_content", return_value=fake_response
        ) as mock_gen:
            mock_conn.user = self.bot_user
            msg = self._make_message(f"<@!{self.bot_user.id}> 富士山の高さは？")
            await bot.on_message(msg)

            mock_gen.assert_called_once_with("富士山の高さは？")
            msg.reply.assert_awaited_once_with("富士山は3776mです。")
            msg.channel.send.assert_not_called()

    async def test_on_message_splits_long_reply_into_chunks(self):
        long_text = "あ" * 2500
        fake_response = SimpleNamespace(text=long_text)
        with patch.object(bot.client, "_connection") as mock_conn, patch.object(
            bot, "generate_content", return_value=fake_response
        ):
            mock_conn.user = self.bot_user
            msg = self._make_message(f"<@{self.bot_user.id}> 長文を書いて")
            await bot.on_message(msg)

            msg.reply.assert_awaited_once_with("あ" * 2000)
            msg.channel.send.assert_awaited_once_with("あ" * 500)

    async def test_on_message_handles_api_exception_gracefully(self):
        with patch.object(bot.client, "_connection") as mock_conn, patch.object(
            bot, "generate_content", side_effect=RuntimeError("API Error with key")
        ):
            mock_conn.user = self.bot_user
            msg = self._make_message(f"<@{self.bot_user.id}> エラーテスト")
            await bot.on_message(msg)

            msg.reply.assert_awaited_once_with(
                "申し訳ありません、エラーが発生しました。ログを確認してください。"
            )

    async def test_on_message_ignores_when_client_user_is_none(self):
        with patch.object(bot.client, "_connection") as mock_conn:
            mock_conn.user = None
            msg = self._make_message("<@999999999> テスト")
            await bot.on_message(msg)
            msg.reply.assert_not_called()
            msg.channel.send.assert_not_called()

    async def test_on_message_handles_discord_http_exception_on_error_reply(self):
        fake_http_response = SimpleNamespace(status=403, reason="Forbidden")
        http_error = discord.HTTPException(fake_http_response, "Missing Permissions")
        with patch.object(bot.client, "_connection") as mock_conn, patch.object(
            bot, "generate_content", side_effect=RuntimeError("API Error")
        ):
            mock_conn.user = self.bot_user
            msg = self._make_message(f"<@{self.bot_user.id}> 権限エラーテスト")
            msg.reply = AsyncMock(side_effect=http_error)
            await bot.on_message(msg)
            msg.reply.assert_awaited_once()


class TestMainEntryPoint(unittest.TestCase):
    def test_main_exits_with_code_1_when_env_vars_missing(self):
        with patch.object(bot, "DISCORD_BOT_TOKEN", None), patch.object(
            bot, "GOOGLE_API_KEY", None
        ), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                bot.main()
            self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
