# Discord Gemini Bot

Discord のメンション `@Bot` に反応して、Gemini のモデル（デフォルト: `gemini-3.6-flash`）で回答する Discord Bot です。
Ubuntu 上で 24 時間稼働できるように `systemctl` でのサービス化に対応しています。

## 🔧 セットアップ方法 (Ubuntu)

### 1. 必要なパッケージのインストール

まず、Python 3（3.11 以上推奨）と pip、venv、git がインストールされていることを確認します（必要に応じてインストール）。
```bash
sudo apt update
sudo apt install git python3 python3-pip python3-venv
```

### 2. 環境の構築

リポジトリをクローンしてプロジェクトディレクトリへ移動し、仮想環境（venv）を作成してライブラリをインストールします。
（※仮想環境内の `setuptools` 等の脆弱性を防ぐため、最初に `pip` と `setuptools` をアップグレードすることを推奨します）
```bash
git clone https://github.com/tenchu0314/discord-gemini-bot.git
cd discord-gemini-bot
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip setuptools
pip install -r requirements.txt
```

### 3. 環境変数の設定

`.env.sample` をコピーして `.env` を作成し、それぞれのトークン・APIキーを書き込みます。第三者による読み取りを防ぐため、パーミッションを `600` に制限してください。
```bash
cp .env.sample .env
chmod 600 .env
nano .env  # もしくは vim 等で編集
```

- `DISCORD_BOT_TOKEN`: Discord Developer Portal で作成した Bot の Token
  - **※重要:** Discord Developer Portal の **Bot** 設定ページにある **Privileged Gateway Intents** にて、**`MESSAGE CONTENT INTENT`** を必ず有効（ON）にしてください（メッセージ内容の読み取りに必須です）。
- `GOOGLE_API_KEY`: Google AI Studio で取得した API キー
- `GEMINI_MODEL`: 使用するモデル名（例: `gemini-2.5-flash`）。未指定の場合は `gemini-3.6-flash` が使用されます。

### 4. システム指示（性格付け）のカスタマイズ (`SOUL.md`)

プロジェクト直下の `SOUL.md` に記述されたテキストが、Gemini API リクエスト時のシステム指示（`system_instruction`）として読み込まれます。
- デフォルトでは `日本語回答する` が設定されています。
- ボットの口調やキャラクター、回答ルールを変更したい場合は `SOUL.md` を編集してください（リクエストごとに読み込まれるため、ボットの再起動なしで反映されます）。

### 5. Systemdサービスへの登録・自動起動設定

Bot を常に動かし続けるため、systemd に登録します。

**※注意:** コピーする前に、`discord-gemini-bot.service` ファイル内の `your_username` やディレクトリパスをご自身の環境に合わせて書き換えてください。
- `User` / `Group`: 実行する Linux ユーザー名・グループ名
- `WorkingDirectory`: プロジェクトディレクトリの絶対パス（例: `/home/your_username/discord-gemini-bot`）
- `EnvironmentFile`: `.env` ファイルの絶対パス（例: `/home/your_username/discord-gemini-bot/.env`）
- `ExecStart`: 仮想環境（venv）の Python と `bot.py` の絶対パス
```ini
ExecStart=/home/your_username/discord-gemini-bot/venv/bin/python /home/your_username/discord-gemini-bot/bot.py
```

サービスファイルをシステムディレクトリにコピーします。
```bash
sudo cp discord-gemini-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
```

サービスの自動起動（サーバ再起動時に自動で立ち上がる）を有効にします。
```bash
sudo systemctl enable discord-gemini-bot
```

Bot を起動します。
```bash
sudo systemctl start discord-gemini-bot
```

### 6. 稼働状況の確認

ログを確認して、問題なく起動しているか確認します。
```bash
sudo systemctl status discord-gemini-bot
```

リアルタイムでログを見たい場合：
```bash
journalctl -u discord-gemini-bot -f
```

## 📝 機能
- **メンション応答**: ボットに `@Bot` でメンション（ニックネームメンションを含む）すると、テキストを Gemini API に送信して返答します。
- **Google 検索グラウンディング (`google_search`)**: Gemini の Google Search ツールを有効化しており、最新情報に基づいた回答が可能です。
- **システム指示のカスタマイズ (`SOUL.md`)**: `SOUL.md` を編集することで、ボットの性格や回答スタイル（デフォルト: `日本語回答する`）を柔軟に設定できます。
- **推論プロセスの自動除去**: 内部思考プロセス（`<think>...</think>` や `<thought>...</thought>` タグ、および出力途切れ時の未閉じタグ）が含まれた場合は、Discord の返信から自動的に除去します。
- **長文メッセージの自動分割送信**: 2000 文字を超える長文回答が返ってきた場合は、Discord の文字数制限に合わせて 2000 文字以下（2000 文字ごと）に分割して連投します。
- **自動リトライ機能**: Gemini API 呼び出し時に一時的なエラーが発生した場合、指数バックオフ（Exponential Backoff）を用いて最大 5 回（計 6 回試行）まで自動リトライします。
- **セキュリティ対策**: 他のボットからのメッセージを無視してボット間の無限ループを防止し、`AllowedMentions.none()` により `@everyone` / `@here` やロール・ユーザーへの意図しないメンション汚染（プロンプトインジェクション悪用）を無効化しています。

