# Student AI Assistant

学生生活を支援する AI スマートスピーカー。
Raspberry Pi をサーバー、使わなくなった Android スマートフォンを画面・マイク・スピーカーとして使い、
Gemini API を司令塔に、予定・経路・天気・リマインダーを統合して「その日の行動」を提案します。

> 「明日9時から学校なんやけど何時に家出たらいい？」
> → 「明日は9時から授業があります。朝は雨の予報なので、7時40分ごろに出るのがおすすめです。」

---

## コマンド一つで起動

```bash
./run.sh
```

Windows の場合:

```powershell
.\run.ps1
```

このスクリプトが、仮想環境の作成 → 依存関係のインストール → `.env` の用意 → サーバー起動までをまとめて行います。
2回目以降は依存関係を飛ばして数秒で立ち上がります。

起動したら、同じ Wi-Fi のスマートフォンから表示された URL を開いてください。

### 最初にやること

1. https://aistudio.google.com/apikey で Gemini API キーを取得する
2. `.env` を開いて `GEMINI_API_KEY=` の行に貼り付ける
3. `./run.sh` をもう一度実行する

キーが無くても画面は開きます（天気・予定・リマインダーは動きます）が、AI との会話はできません。

---

## スマートフォンで音声入力を使うには HTTPS が必要

ブラウザのマイク（Web Speech API）と PWA インストールは、**セキュアコンテキスト**でしか動きません。
`http://192.168.x.x:8000` のような LAN の HTTP で開くと、音声ボタンがテキスト入力に切り替わります。

自己署名証明書を作れば HTTPS になり、音声入力が使えます。

```bash
python scripts/make_cert.py
```

表示に従って `.env` の次の2行のコメントを外し、再起動してください。

```
SSL_CERT_FILE=config/cert.pem
SSL_KEY_FILE=config/key.pem
```

スマートフォンで最初に開いたとき「この接続ではプライバシーが保護されません」と出ますが、
家庭内 LAN で自分の Raspberry Pi に繋いでいるだけなので、「詳細設定」→「アクセスする」で進めます。

---

## できること

| 機能 | 例 | データの出どころ |
|---|---|---|
| 出発時刻の提案 | 「明日何時に家出たらいい？」 | 予定 + 経路 + 天気 + 設定 |
| 経路案内 | 「学校までどう行けばいい？」 | Google Routes API |
| 天気 | 「今日傘いる？」 | Open-Meteo（キー不要） |
| 予定確認 | 「今日の予定は？」 | Google カレンダー / ローカル予定表 |
| リマインダー | 「20分後に洗濯物教えて」 | SQLite |
| 一般質問・学習支援 | 「Javaのinterfaceって何？」 | Gemini |

### 推測させない設計

時刻・予定・経路・天気・リマインダーは、**Gemini に推測させず必ずツール経由で実データを取得**します
（仕様書 22章）。取得できなかったときは架空の情報を作らず、こう答えます。

> 「現在、経路情報を取得できませんでした。」

---

## API キーの要不要

| 機能 | キー | 未設定のとき |
|---|---|---|
| Gemini（会話） | **必須** | 会話不可。画面は開く |
| Open-Meteo（天気） | 不要 | — |
| Google Routes（経路） | 任意 | 「経路情報を取得できませんでした」と応答 |
| Google カレンダー | 任意 | アプリ内のローカル予定表を使う |

キーはすべて Raspberry Pi 側の `.env` にだけ置き、スマートフォンには一切渡しません（仕様書 21章）。
`.env` は `.gitignore` 済みです。

### 経路検索を有効にする

1. Google Cloud Console で **Routes API** を有効にする
2. API キーを作る
3. `.env` の `GOOGLE_MAPS_API_KEY=` に貼る
4. アプリの「設定」画面で自宅住所と学校の所在地を登録する

### Google カレンダーを繋ぐ

1. Google Cloud Console で **Calendar API** を有効にする
2. OAuth 2.0 クライアント ID（種類: **デスクトップアプリ**）を作る
3. JSON を `config/google_client_secret.json` として保存する
4. 認証する

```bash
python scripts/google_auth.py
```

ブラウザで許可すると `config/google_token.json` が作られ、予定の取得元が Google カレンダーに切り替わります。
トークンは Raspberry Pi 内にのみ保存されます。

---

## Raspberry Pi で自動起動する

```bash
sudo ./scripts/install_service.sh
```

systemd に登録され、電源投入時に自動で立ち上がります。

```bash
systemctl status student-ai-assistant     # 状態
journalctl -u student-ai-assistant -f     # ログ
sudo systemctl restart student-ai-assistant
```

スマートフォンからは `http://raspberrypi.local:8000` でアクセスできます。
Chrome のメニューから「ホーム画面に追加」を選ぶと、通常のアプリのように起動できます（PWA）。

---

## 構成

```
スマートフォン ──Wi-Fi/HTTPS──▶ Raspberry Pi 5 ──▶ Gemini API
（画面・マイク・スピーカー）      （FastAPI・SQLite）      │
                                                    Tool Calling
                                                         │
                                       ┌─────────────────┼─────────────────┐
                                  Google Routes     Google Calendar     Open-Meteo
```

音声認識と読み上げは端末内の Web Speech API で行うため、
常時マイク音声を外部へ送りません（仕様書 20章・24章）。

```
student-ai-assistant/
├── backend/
│   ├── main.py            FastAPI 起動・静的配信
│   ├── config.py          .env の読み込み
│   ├── user_settings.py   ユーザー設定（config/settings.json）
│   ├── ai/                Gemini クライアントとシステムプロンプト
│   ├── tools/             Tool Calling で呼ばれる7つの機能
│   ├── audio/             サーバー側 STT/TTS（任意）
│   ├── database/          SQLite
│   └── api/               chat / voice / settings / dashboard
├── frontend/              Web UI（PWA）
├── scripts/               証明書・Google認証・systemd・アイコン生成
├── tests/                 標準ライブラリのみのテスト
├── run.sh / run.ps1       ワンコマンド起動
└── .env.example
```

---

## Tool Calling で使えるツール

| ツール | 役割 |
|---|---|
| `get_current_time` | Raspberry Pi のシステム時刻（Gemini に推測させない） |
| `get_user_profile` | 学校名・授業開始時刻・余裕時間などの設定 |
| `get_calendar` | 指定日の予定 |
| `get_route` | 経路・所要時間。到着時刻から逆算した出発時刻も返す |
| `get_weather` | 天気・気温・降水確率 |
| `create_reminder` | リマインダー登録（「20分後」などの相対指定に対応） |
| `get_reminders` | 未通知のリマインダー一覧 |

---

## 開発

```bash
python -m unittest discover -s tests     # テスト
RELOAD=1 ./run.sh                        # コード変更を自動反映
python scripts/make_icons.py             # PWA アイコンを作り直す
```

任意機能（サーバー側の音声認識・音声合成）を使う場合:

```bash
pip install -r requirements-optional.txt
```

主な API エンドポイント:

| メソッド | パス | 内容 |
|---|---|---|
| `POST` | `/api/chat` | 会話（Tool Calling を含む） |
| `GET` | `/api/home` | ホーム画面用のまとめ取得 |
| `GET` | `/api/calendar` `/api/weather` `/api/route` | 各機能の直接取得 |
| `GET/POST/DELETE` | `/api/reminders` | リマインダー |
| `GET/PUT` | `/api/settings` | ユーザー設定 |
| `GET` | `/api/status` | どの機能が有効かの診断 |
| `GET` | `/docs` | 自動生成される API ドキュメント |

---

## プライバシー

- マイク音声は既定でサーバーに保存しません（`SAVE_AUDIO=0`）
- 会話履歴は SQLite に保存され、設定画面から全削除できます（`SAVE_HISTORY=0` で保存自体を止められます）
- 住所などのセンシティブな情報は `config/settings.json` に置かれ、Git 管理外です
- Service Worker は画面の骨組みだけをキャッシュし、API 応答はキャッシュしません
  （古い天気や予定を表示しないため）

---

## 動作環境

- Raspberry Pi 5（メモリ 4GB 以上、8GB 推奨） / Raspberry Pi OS 64bit
- Python 3.10 以上
- スマートフォン側は Chrome を推奨（音声入力に Web Speech API を使うため）

Windows / macOS でもそのまま動くので、Raspberry Pi を用意する前に手元で試せます。

---

## ロードマップ

- **Version 1.0**（実装済み）会話・音声入出力・天気・経路・カレンダー・出発時刻提案・履歴・設定・リマインダー・PWA
- **Version 1.1** ウェイクワード（openWakeWord）・運行情報・回答のストリーミング表示・より自然な音声合成
- **Version 2.0** スマートホーム連携（Home Assistant）・カメラ・各種センサー・長期記憶
