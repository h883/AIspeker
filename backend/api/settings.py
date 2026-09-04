"""設定 API（仕様書 17章）。住所などの値は Raspberry Pi 内にのみ保存する。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend import config, user_settings
from backend.ai import gemini
from backend.tools import calendar as calendar_tool

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


@router.get("/settings")
def get_settings() -> dict:
    return {"ok": True, "settings": user_settings.load(), "defaults": user_settings.DEFAULTS}


@router.put("/settings")
def update_settings(payload: SettingsUpdate) -> dict:
    return {"ok": True, "settings": user_settings.save(payload.values)}


@router.get("/status")
def status() -> dict:
    """どの機能が使える状態かをフロントに伝える（設定画面の診断表示用）。"""
    return {
        "ok": True,
        "gemini": gemini.is_configured(),
        "gemini_model": config.GEMINI_MODEL,
        "routes": bool(config.GOOGLE_MAPS_API_KEY),
        "weather": True,
        "google_calendar": calendar_tool.google_connected(),
        "calendar_source": user_settings.get("calendar_source"),
        "save_history": config.SAVE_HISTORY,
        "save_audio": config.SAVE_AUDIO,
        "timezone": config.TIMEZONE,
    }
