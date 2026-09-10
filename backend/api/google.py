"""Google カレンダー連携の認証を、スマートフォンの画面だけで完了させる。

なぜこの形なのか:

Google の「デスクトップアプリ」クライアントが許すリダイレクト先は
ループバック（http://127.0.0.1:ポート）だけである。スマートフォンで同意画面を
開くと、その 127.0.0.1 はスマートフォン自身を指すため、Raspberry Pi 側で
待ち受けても届かない。これが scripts/google_auth.py をターミナルで
実行しなければならなかった理由。

そこで待受は用意しない。許可した直後にブラウザのアドレス欄へ残る URL を
利用者に貼り付けてもらい、そこに含まれる code をサーバーが交換する。
Google はリダイレクト先へ実際に到達したかどうかを検証しないので成立する。
（貼り付け用の urn:ietf:wg:oauth:2.0:oob は 2022 年に廃止されたため使えない）

トークンは Raspberry Pi 内の config/google_token.json にのみ保存し、
画面へは「連携済みかどうか」しか返さない（仕様書 21章）。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from backend import config, user_settings
from backend.tools import calendar as calendar_tool
from backend.tools.calendar import GOOGLE_SCOPES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/setup/google", tags=["google"])

# 実際には誰も待ち受けない。Google に「許可のあとここへ返す」と伝えるためだけの値。
# 認可時と交換時で同一である必要があるので定数にしておく。
REDIRECT_URI = "http://127.0.0.1:8765/"

# 「認証をはじめる」から「連携する」までの間だけ Flow を保持する。
# 家庭内の単一プロセスで動く前提。サーバーを再起動したらやり直しになる。
_pending: dict[str, tuple[Any, float]] = {}
PENDING_TTL_SECONDS = 15 * 60


class Redirected(BaseModel):
    redirected_url: str = ""
    state: str = ""


def _client_secret_path() -> Path:
    return Path(config.GOOGLE_OAUTH_CLIENT_SECRET_FILE)


def _token_path() -> Path:
    return Path(config.GOOGLE_OAUTH_TOKEN_FILE)


def _prune() -> None:
    limit = time.time() - PENDING_TTL_SECONDS
    for key in [k for k, (_flow, created) in _pending.items() if created < limit]:
        _pending.pop(key, None)


def state() -> dict[str, Any]:
    return {
        "client_secret_saved": _client_secret_path().exists(),
        "connected": calendar_tool.google_connected(),
        "calendar_source": user_settings.get("calendar_source"),
        "redirect_uri": REDIRECT_URI,
    }


@router.get("")
def get_state() -> dict:
    return {"ok": True, **state()}


@router.post("/client-secret")
async def upload_client_secret(file: UploadFile = File(...)) -> dict:
    """OAuth クライアントの JSON を受け取る。ファイル転送を画面から行えるようにする。"""
    raw = await file.read()
    if not raw:
        return {"ok": False, "error": "ファイルが空です。"}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False,
                "error": "JSON として読めませんでした。ダウンロードしたファイルをそのまま選んでください。"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "OAuth クライアントの JSON ではないようです。"}

    if "web" in data and "installed" not in data:
        return {"ok": False,
                "error": "種類が「ウェブアプリケーション」になっています。"
                         "「デスクトップアプリ」でクライアントIDを作り直してください。"}
    section = data.get("installed") or {}
    if not section.get("client_id"):
        return {"ok": False,
                "error": "client_id が見つかりません。OAuth クライアントの JSON か確認してください。"}

    path = _client_secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    logger.info("Google OAuth クライアントを保存しました: %s", path)   # 中身は出さない
    return {"ok": True, **state()}


@router.post("/start")
def start() -> dict:
    """同意画面の URL を作って返す。実際に開くのは利用者のブラウザ。"""
    if not _client_secret_path().exists():
        return {"ok": False, "error": "先に OAuth クライアントの JSON を登録してください。"}
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        return {"ok": False,
                "error": "google-auth-oauthlib が入っていません。"
                         "pip install -r requirements.txt を実行してください。"}

    try:
        flow = Flow.from_client_secrets_file(
            str(_client_secret_path()), scopes=GOOGLE_SCOPES, redirect_uri=REDIRECT_URI
        )
        # offline + consent がないと更新用トークンが返らず、1時間で切れてしまう
        auth_url, auth_state = flow.authorization_url(
            access_type="offline", prompt="consent", include_granted_scopes="true"
        )
    except Exception as exc:  # noqa: BLE001 - 原因は利用者に伝えて再試行させる
        logger.exception("認証の開始に失敗しました")
        return {"ok": False, "error": f"認証を開始できませんでした（{exc.__class__.__name__}）。"}

    _prune()
    _pending[auth_state] = (flow, time.time())
    return {"ok": True, "auth_url": auth_url, "state": auth_state, "redirect_uri": REDIRECT_URI}


def extract_code(text: str) -> tuple[str, str, str]:
    """貼り付けられた内容から (code, state, error) を取り出す。

    URL ごと貼られても、code だけ貼られても受け取れるようにする。
    """
    value = (text or "").strip()
    if not value:
        return "", "", ""
    if "?" in value or value.lower().startswith("http"):
        query = parse_qs(urlparse(value).query)
        return (query.get("code", [""])[0].strip(),
                query.get("state", [""])[0].strip(),
                query.get("error", [""])[0].strip())
    return value, "", ""


@router.post("/finish")
def finish(payload: Redirected) -> dict:
    """貼り付けられた URL の code をトークンへ交換する。"""
    code, url_state, error = extract_code(payload.redirected_url)
    if error:
        return {"ok": False, "error": f"許可されませんでした（{error}）。もう一度お試しください。"}
    if not code:
        return {"ok": False,
                "error": "URL の中に認証コードが見つかりませんでした。"
                         "アドレス欄の URL をすべてコピーして貼り付けてください。"}

    _prune()
    key = url_state or payload.state
    entry = _pending.pop(key, None) if key else None
    if entry is None and len(_pending) == 1:
        # state が欠けていても、進行中がひとつだけなら取り違えようがない
        _, entry = _pending.popitem()
    if entry is None:
        return {"ok": False,
                "error": "認証の情報が見つかりませんでした。"
                         "「認証をはじめる」からやり直してください。"}

    flow, _created = entry
    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # noqa: BLE001 - 貼り間違い・期限切れなど
        logger.warning("トークン交換に失敗しました: %s", exc.__class__.__name__)
        return {"ok": False,
                "error": f"認証コードを交換できませんでした（{exc.__class__.__name__}）。"
                         "URL を貼り直すか、最初からやり直してください。"}

    creds = flow.credentials
    if not getattr(creds, "refresh_token", None):
        # これが無いと1時間で切れて毎回やり直しになる
        return {"ok": False,
                "error": "更新用のトークンが返りませんでした。"
                         "Google アカウントのアクセス権を一度削除してから、やり直してください。"}

    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    user_settings.save({"calendar_source": "google"})
    logger.info("Google カレンダーと連携しました")
    return {"ok": True, "message": "Google カレンダーと連携しました。", **state()}


@router.post("/disconnect")
def disconnect() -> dict:
    """連携を解除する。トークンを消し、予定の取得元をローカルへ戻す。"""
    _token_path().unlink(missing_ok=True)
    _pending.clear()
    user_settings.save({"calendar_source": "local"})
    return {"ok": True, "message": "連携を解除しました。", **state()}
