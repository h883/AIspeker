"""現在日時。Gemini に推測させず Raspberry Pi のシステム時刻を使う（仕様書 8章）。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from backend import config

WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]


def tz() -> ZoneInfo:
    try:
        return ZoneInfo(config.TIMEZONE)
    except Exception:  # noqa: BLE001 - tzdata 未導入環境ではローカル時刻に落とす
        return ZoneInfo("UTC")


def now() -> datetime:
    return datetime.now(tz())


def get_current_time() -> dict[str, Any]:
    current = now()
    return {
        "ok": True,
        "iso": current.isoformat(timespec="seconds"),
        "date": current.strftime("%Y-%m-%d"),
        "time": current.strftime("%H:%M"),
        "weekday": WEEKDAYS_JA[current.weekday()],
        "timezone": str(tz()),
    }


def resolve_date(value: str | None) -> str:
    """「今日」「明日」「2026-09-05」などを YYYY-MM-DD へ正規化する。"""
    today = now().date()
    if not value:
        return today.isoformat()
    text = value.strip().lower()
    offsets = {
        "today": 0, "今日": 0, "きょう": 0, "本日": 0,
        "tomorrow": 1, "明日": 1, "あした": 1, "あす": 1,
        "明後日": 2, "あさって": 2,
        "yesterday": -1, "昨日": -1, "きのう": -1,
    }
    if text in offsets:
        return (today + timedelta(days=offsets[text])).isoformat()
    # 年が無い「9/5」形式は今年として扱う（年を補ってから解釈する）
    candidates = [(text, "%Y-%m-%d"), (text, "%Y/%m/%d")]
    if text.count("-") == 1:
        candidates.append((f"{today.year}-{text}", "%Y-%m-%d"))
    if text.count("/") == 1:
        candidates.append((f"{today.year}/{text}", "%Y/%m/%d"))

    for value, fmt in candidates:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return today.isoformat()
