"""Google カレンダー連携の初回認証（仕様書 12章・21章）。

事前準備:
  1. Google Cloud Console で Calendar API を有効にする
  2. OAuth 2.0 クライアント ID（種類: デスクトップアプリ）を作成する
  3. JSON をダウンロードして config/google_client_secret.json として保存する

実行:
  python scripts/google_auth.py

ブラウザが開くので許可すると config/google_token.json が作られる。
トークンは Raspberry Pi 内にのみ保存され、スマートフォンには渡らない。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, user_settings  # noqa: E402
from backend.tools.calendar import GOOGLE_SCOPES  # noqa: E402


def main() -> int:
    secret_path = Path(config.GOOGLE_OAUTH_CLIENT_SECRET_FILE)
    token_path = Path(config.GOOGLE_OAUTH_TOKEN_FILE)

    if not secret_path.exists():
        print(f"クライアントシークレットが見つかりません: {secret_path}")
        print("Google Cloud Console でデスクトップアプリ用の OAuth クライアントを作成し、")
        print("ダウンロードした JSON を上記のパスに保存してから、もう一度実行してください。")
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("google-auth-oauthlib が入っていません。pip install -r requirements.txt を実行してください。")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), GOOGLE_SCOPES)
    # Raspberry Pi をヘッドレスで使う場合は console 認証にフォールバックする
    try:
        creds = flow.run_local_server(port=0, open_browser=True)
    except Exception:  # noqa: BLE001
        print("ブラウザを開けませんでした。表示された URL に別の端末からアクセスしてください。")
        creds = flow.run_local_server(port=8765, open_browser=False, bind_addr="0.0.0.0")

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    user_settings.save({"calendar_source": "google"})

    print(f"認証しました。トークンを保存しました: {token_path}")
    print("予定の取得元を Google カレンダーに切り替えました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
