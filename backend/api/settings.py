"""設定 API（仕様書 17章）。住所などの値は Raspberry Pi 内にのみ保存する。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend import config, user_settings
from backend.api.setup import apply_profile
from backend.tools import calendar as calendar_tool

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


@router.get("/settings")
def get_settings() -> dict:
    return {"ok": True, "settings": user_settings.load(), "defaults": user_settings.DEFAULTS}


@router.put("/settings")
def update_settings(payload: SettingsUpdate) -> dict:
    # 天気の地域変更時の座標取り直しなど、セットアップと同じ処理を通す
    return apply_profile(payload.values)


@router.get("/status")
def status() -> dict:
    """どの機能が使える状態かをフロントに伝える（設定画面の診断表示用）。"""
    settings = user_settings.load()
    return {
        "ok": True,
        "needs_setup": not settings.get("setup_completed"),
        "gemini": bool(config.GEMINI_API_KEY),
        "gemini_model": config.GEMINI_MODEL,
        "routes": bool(config.GOOGLE_MAPS_API_KEY),
        "weather": True,
        "google_calendar": calendar_tool.google_connected(),
        "calendar_source": settings.get("calendar_source"),
        "save_history": config.SAVE_HISTORY,
        "save_audio": config.SAVE_AUDIO,
        "timezone": config.TIMEZONE,
    }
