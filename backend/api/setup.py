"""初回セットアップ API。

.env を手で編集しなくても、ブラウザからAPIキーと基本設定を登録できるようにする。
書き込んだ設定は config.reload() で即座に反映するため、サーバーの再起動は要らない。

APIキーの値は Raspberry Pi 側の .env にのみ保存し、
画面へは「設定済みかどうか」しか返さない（仕様書 21章）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend import config, user_settings
from backend.ai import gemini
from backend.tools import calendar as calendar_tool
from backend.tools import weather as weather_tool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/setup", tags=["setup"])


class KeyTest(BaseModel):
    api_key: str = ""
    model: str = ""


class KeySave(BaseModel):
    gemini_api_key: str = ""
    gemini_model: str = ""
    google_maps_api_key: str = ""


class ProfileSave(BaseModel):
    values: dict = Field(default_factory=dict)


def _state() -> dict:
    settings = user_settings.load()
    return {
        "needs_setup": not settings.get("setup_completed"),
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "gemini_model": config.GEMINI_MODEL,
        "maps_configured": bool(config.GOOGLE_MAPS_API_KEY),
        "google_calendar_connected": calendar_tool.google_connected(),
        "env_path": str(config.ENV_PATH),
        "settings": settings,
    }


@router.get("")
def get_setup() -> dict:
    """セットアップ画面の初期表示用。キーの中身は返さない。"""
    return {"ok": True, **_state()}


@router.post("/test-gemini")
def test_gemini(payload: KeyTest) -> dict:
    """保存する前に、入力されたキーが実際に通るか確かめる。"""
    return gemini.verify_key(payload.api_key, payload.model)


@router.post("/models")
def models(payload: KeyTest) -> dict:
    """このキーで使えるモデルの一覧。選択肢をここから作る。"""
    return gemini.list_models(payload.api_key)


@router.post("/keys")
def save_keys(payload: KeySave) -> dict:
    """APIキーを .env へ書き込み、その場で反映する。"""
    saved = config.update_env({
        "GEMINI_API_KEY": payload.gemini_api_key,
        "GEMINI_MODEL": payload.gemini_model,
        "GOOGLE_MAPS_API_KEY": payload.google_maps_api_key,
    })
    if saved:
        # ログにも鍵そのものは出さない
        logger.info("設定を保存しました: %s", ", ".join(saved))
    return {"ok": True, "saved": saved, **_state()}


def apply_profile(values: dict) -> dict:
    """基本設定を保存する。設定画面からの保存でも同じ処理を通す。

    天気の地名が引けなかった場合でも、他の項目は保存する。
    地名ひとつのために名前や住所の入力をやり直させないため。
    """
    values = dict(values)
    warning = ""

    # 天気の地域が変わったら緯度経度も取り直す（古い座標のまま予報を出さないため）
    location = str(values.get("weather_location") or "").strip()
    if location and location != user_settings.get("weather_location"):
        coords = weather_tool.geocode(location)
        if coords:
            values["weather_lat"], values["weather_lon"] = coords
        else:
            # 座標を更新できないので、地名も前のままにしておく（食い違いを防ぐ）
            values.pop("weather_location", None)
            warning = (
                f"「{location}」の場所が見つかりませんでした。"
                f"天気は「{user_settings.get('weather_location')}」のままにしています。"
                "市区町村名（例: 大阪市）で入力し直してください。"
            )

    return {"ok": True, "warning": warning, "settings": user_settings.save(values)}


@router.post("/profile")
def save_profile(payload: ProfileSave) -> dict:
    return apply_profile(payload.values)


@router.post("/complete")
def complete() -> dict:
    """セットアップ完了。次回からはホーム画面で始まる。"""
    user_settings.save({"setup_completed": True})
    return {"ok": True, **_state()}


@router.post("/reopen")
def reopen() -> dict:
    """設定画面から、あとでセットアップをやり直したいとき用。"""
    user_settings.save({"setup_completed": False})
    return {"ok": True, **_state()}
