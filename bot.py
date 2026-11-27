import os
import re
import sys
import asyncio
from pathlib import Path
from urllib.parse import quote
import discord
from google import genai
from google.genai import errors, types
from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
SOUL_FILE = BASE_DIR / "SOUL.md"
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
MAX_RETRY_ATTEMPTS = 6
GEMINI_TIMEOUT_MS = 60_000
MAX_CONCURRENT_REQUESTS = 5
RETRYABLE_CLIENT_STATUS_CODES = frozenset({408, 429})

# .envファイルがあれば読み込む（ローカルテスト用）
load_dotenv(dotenv_path=ENV_FILE)

# 環境変数の取得
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = (os.getenv("GEMINI_MODEL") or "").strip() or DEFAULT_GEMINI_MODEL

# Gemini クライアント（起動時または初回呼び出し時に初期化）
gemini_client = None

# 同時リクエスト数制限（スレッドプール枯渇・APIクォータ急増の緩和）
request_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)


def redact_secrets(message):
    """ログ出力文字列からAPIキーやBotトークンをマスクする"""
    text = str(message)
    for secret in (
        GOOGLE_API_KEY,
        DISCORD_BOT_TOKEN,
        os.getenv("GOOGLE_API_KEY"),
        os.getenv("DISCORD_BOT_TOKEN"),
    ):
        if secret:
            text = text.replace(secret, "[REDACTED]")
            encoded = quote(secret, safe="")
            if encoded and encoded != secret:
                text = text.replace(encoded, "[REDACTED]")
    # URLクエリ文字列内のキーやトークンも念のためマスク
    text = re.sub(
        r"([?&](?:key|api_key|token)=)([^&\s'\"`]+)",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    # Google API Key 形式（AIza...）が直接含まれた場合もマスク
    text = re.sub(r"\bAIza[0-9A-Za-z_-]{30,}\b", "[REDACTED]", text)
    return text


def is_retryable_error(exc):
    """一時的なエラーのみリトライ対象と判定する"""
    if isinstance(exc, errors.ClientError):
        return getattr(exc, "code", None) in RETRYABLE_CLIENT_STATUS_CODES
    if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)):
        return False
    if isinstance(exc, RuntimeError) and "GOOGLE_API_KEY" in str(exc):
        return False
    return True


def get_gemini_client():
    """Gemini クライアントを取得（未初期化の場合は初期化する）"""
    global gemini_client
    if gemini_client is not None:
        return gemini_client
    api_key = GOOGLE_API_KEY or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set.")
    gemini_client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
    )
    return gemini_client


def get_system_instruction():
    """SOUL.md からシステム指示（性格付け）を読み込む"""
    soul_path = Path(SOUL_FILE)
    if soul_path.exists():
        try:
            with open(soul_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return content if content else None
        except Exception as e:
            print(f"Error reading {soul_path}: {redact_secrets(e)}")
    return None


@retry(
    stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(is_retryable_error),
    reraise=True,
    before_sleep=lambda retry_state: print(
        f"Retrying Gemini API call (attempt {retry_state.attempt_number}/{MAX_RETRY_ATTEMPTS - 1})..."
    ),
)
def generate_content(prompt):
    """Gemini API にリクエストを送信 (一時的エラー時のリトライ機能付き)"""
    system_instruction = get_system_instruction()
    active_client = get_gemini_client()
    model_name = (GEMINI_MODEL or "").strip() or DEFAULT_GEMINI_MODEL
    return active_client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            system_instruction=system_instruction,
        ),
    )


# Discord クライアントの初期化設定
intents = discord.Intents.default()
intents.message_content = True  # メッセージの内容を読み取るために必要
client = discord.Client(
    intents=intents,
    allowed_mentions=discord.AllowedMentions.none(),
)


def split_message(text, limit=2000):
    """Discordの2000文字制限に合わせてメッセージを分割する"""
    if limit <= 0:
        raise ValueError("limit must be greater than 0")
    if not text:
        return []
    msgs = []
    # Discordの2000文字制限を超えないように分割
    while len(text) > 0:
        msgs.append(text[:limit])
        text = text[limit:]
    return msgs


def remove_thinking(text):
    """推論ブロック（<think>...</think> など）を除去する"""
    if not text:
        return ""
    flags = re.DOTALL | re.IGNORECASE
    # <think>...</think> や <thought>...</thought> のようなタグを中身ごと削除
    text = re.sub(r"<think>.*?</think>", "", text, flags=flags)
    text = re.sub(r"<thought>.*?</thought>", "", text, flags=flags)
    # 出力途切れ等で閉じタグがない未完の <think> / <thought> ブロックも末尾まで削除
    text = re.sub(r"<think>.*$", "", text, flags=flags)
    text = re.sub(r"<thought>.*$", "", text, flags=flags)
    return text.strip()


@client.event
async def on_ready():
    print(f"Logged in as {client.user} ({client.user.id})")
    print("Bot is ready and waiting for mentions!")


@client.event
async def on_message(message):
    if client.user is None:
        return

    # ボット自身および他のボットからのメッセージは無視（無限ループ防止）
    if message.author == client.user or getattr(message.author, "bot", False):
        return

    # ボットへのメンションがあるか確認
    if client.user in message.mentions:
        # メッセージからメンション部分（通常 <@ID> およびニックネーム <@!ID>）を削除してプロンプトを作成
        # 例: "@DiscordBot こんにちは" -> "こんにちは"
        prompt = re.sub(rf"<@!?{client.user.id}>", "", message.content).strip()

        if not prompt:
            try:
                await message.channel.send(
                    "何か質問を入力してください。（例: `@Botの名前 富士山の高さは？`）"
                )
            except discord.HTTPException as e:
                print(f"Error sending empty-prompt guidance: {redact_secrets(e)}")
            return

        # 同時実行数を制限しつつ、Discordの「入力中...」ステータスを表示
        async with request_semaphore:
            async with message.channel.typing():
                try:
                    # Gemini API にリクエストを送信 (ブロッキング処理を回避するため別スレッドで実行)
                    response = await asyncio.to_thread(generate_content, prompt)

                    # レスポンスから推論部分を削除
                    reply_text = remove_thinking(getattr(response, "text", None))

                    if not reply_text:
                        reply_text = "（回答が空になりました）"

                    # 2000文字制限で分割して送信
                    for index, chunk in enumerate(split_message(reply_text)):
                        if index == 0:
                            # 最初のメッセージは返信として送信
                            await message.reply(chunk)
                        else:
                            # 続きのメッセージは通常の投稿として送信
                            await message.channel.send(chunk)

                except Exception as e:
                    print(f"Error during Gemini API call: {redact_secrets(e)}")
                    try:
                        await message.reply(
                            "申し訳ありません、エラーが発生しました。ログを確認してください。"
                        )
                    except discord.HTTPException as reply_err:
                        print(
                            f"Error sending failure reply: {redact_secrets(reply_err)}"
                        )


def main():
    """環境変数を検証してボットを起動する"""
    token = DISCORD_BOT_TOKEN or os.getenv("DISCORD_BOT_TOKEN")
    api_key = GOOGLE_API_KEY or os.getenv("GOOGLE_API_KEY")

    if not token or not api_key:
        print("Error: DISCORD_BOT_TOKEN or GOOGLE_API_KEY is not set.")
        print("環境変数に DISCORD_BOT_TOKEN と GOOGLE_API_KEY を設定してください。")
        sys.exit(1)

    get_gemini_client()
    client.run(token)


# ボットの起動
if __name__ == "__main__":
    main()
