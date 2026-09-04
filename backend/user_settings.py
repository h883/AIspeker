"""ユーザー設定（仕様書 17章）。住所などは Raspberry Pi 内の JSON にのみ保存する。"""
from __future__ import annotations

import json
import threading
from typing import Any

from backend import config

_lock = threading.Lock()

DEFAULTS: dict[str, Any] = {
    "user_name": "",
    "school_name": "学校",
    "school_address": "",
    "school_start_time": "09:00",
    "home_address": "",
    "home_station": "",
    "travel_mode": "transit",          # transit / walking / bicycling / driving
    "buffer_minutes": 10,               # 通常の余裕時間
    "rain_extra_minutes": 10,           # 雨のときの追加余裕
    "weather_location": "大阪市",
    "weather_lat": 34.6937,
    "weather_lon": 135.5023,
    "calendar_source": "local",        # local / google
    "google_calendar_id": "primary",
    "tts_enabled": True,
    "tts_rate": 1.0,
    "save_audio": False,
    "save_history": True,
}


def load() -> dict[str, Any]:
    settings = dict(DEFAULTS)
    if config.SETTINGS_PATH.exists():
        try:
            stored = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                settings.update(stored)
        except (json.JSONDecodeError, OSError):
            pass
    return settings


def save(updates: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        settings = load()
        for key, value in updates.items():
            if key in DEFAULTS:
                settings[key] = value
        config.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_PATH.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return settings


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default if default is not None else DEFAULTS.get(key))
