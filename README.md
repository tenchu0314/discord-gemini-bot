# Discord Gemini Bot

Discord のメンション `@Bot` に反応して、Gemini の `gemini-3.1-flash-lite-preview` モデルで回答する Discord Bot です。
Ubuntu 上で 24 時間稼働できるように `systemctl` でのサービス化に対応しています。

## 🔧 セットアップ方法 (Ubuntu)

### 1. 必要なパッケージのインストール

まず、Python 3 と pip、venv がインストールされていることを確認します（必要に応じてインストール）。
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

### 2. 環境の構築

プロジェクトディレクトリへ移動し、ファイルの用意を行います。
（既にファイルがある場合は不要です）

仮想環境（venv）を作成し、ライブラリをインストールすることをおすすめします。
```bash
cd /home/kawata/discord_gemini_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 環境変数の設定

`.env.sample` をコピーして `.env` を作成し、それぞれのトークンを書き込みます。
```bash
cp .env.sample .env
nano .env  # もしくは vim 等で編集
```

- `DISCORD_BOT_TOKEN`: Discord Developer Portal で作成した Bot の Token
- `GOOGLE_API_KEY`: Google AI Studio で取得した API キー

### 4. Systemdサービスへの登録・自動起動設定

Bot を常に動かし続けるため、systemd に登録します。

**※注意:** `discord-gemini-bot.service` ファイル内で仮想環境の Python を利用する場合は、`ExecStart` の行を以下のように書き換えてください。
```ini
ExecStart=/home/kawata/discord_gemini_bot/venv/bin/python bot.py
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

### 5. 稼働状況の確認

ログを確認して、問題なく起動しているか確認します。
```bash
sudo systemctl status discord-gemini-bot
```

リアルタイムでログを見たい場合：
```bash
journalctl -u discord-gemini-bot -f
```

## 📝 機能
- ボットに `@` でメンションするとテキストをGeminiに送信して返答します。
- 内部思考プロセス（`<think>` タグなど）が含まれた場合は、Discordのメッセージには送られないように除去します。
- 長文が返ってきた場合は 2000 文字未満に分割して連投します。
