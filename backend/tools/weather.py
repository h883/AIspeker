"""天気取得（仕様書 11章）。

既定は Open-Meteo（APIキー不要）。OPENWEATHER_API_KEY を設定すれば OpenWeather も使える。
取得に失敗した場合は ok=False を返し、AI に架空の天気を作らせない（仕様書 22/23章）。
"""
from __future__ import annotations

from typing import Any

import httpx

from backend import config, user_settings
from backend.tools.time import resolve_date

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo の WMO weather code -> 日本語
WMO_JA = {
    0: "快晴", 1: "晴れ", 2: "晴れときどき曇り", 3: "曇り",
    45: "霧", 48: "霧氷",
    51: "弱い霧雨", 53: "霧雨", 55: "強い霧雨",
    56: "着氷性の霧雨", 57: "強い着氷性の霧雨",
    61: "弱い雨", 63: "雨", 65: "強い雨",
    66: "着氷性の雨", 67: "強い着氷性の雨",
    71: "弱い雪", 73: "雪", 75: "強い雪", 77: "霧雪",
    80: "にわか雨", 81: "強いにわか雨", 82: "激しいにわか雨",
    85: "にわか雪", 86: "強いにわか雪",
    95: "雷雨", 96: "雹をともなう雷雨", 99: "激しい雹をともなう雷雨",
}


def _geocode(location: str) -> tuple[float, float] | None:
    try:
        response = httpx.get(
            GEOCODE_URL,
            params={"name": location, "count": 1, "language": "ja", "format": "json"},
            timeout=10,
        )
        response.raise_for_status()
        results = response.json().get("results") or []
    except (httpx.HTTPError, ValueError):
        return None
    if not results:
        return None
    return float(results[0]["latitude"]), float(results[0]["longitude"])


def get_weather(location: str | None = None, date: str | None = None) -> dict[str, Any]:
    settings = user_settings.load()
    target_date = resolve_date(date)
    place = (location or settings.get("weather_location") or "").strip()

    coords: tuple[float, float] | None = None
    if place and place != settings.get("weather_location"):
        coords = _geocode(place)
    if coords is None:
        lat, lon = settings.get("weather_lat"), settings.get("weather_lon")
        if lat is None or lon is None:
            coords = _geocode(place) if place else None
        else:
            coords = (float(lat), float(lon))
    if coords is None:
        return {"ok": False, "error": "天気の地点を特定できませんでした。設定で地域を登録してください。"}

    lat, lon = coords
    try:
        response = httpx.get(
            FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "timezone": config.TIMEZONE,
                "start_date": target_date,
                "end_date": target_date,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                         "precipitation_probability_max,precipitation_sum",
                "hourly": "weather_code,temperature_2m,precipitation_probability",
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": f"天気情報を取得できませんでした（{exc.__class__.__name__}）。"}

    daily = payload.get("daily") or {}
    if not daily.get("time"):
        return {"ok": False, "error": "天気情報を取得できませんでした。"}

    code = (daily.get("weather_code") or [None])[0]
    precip_prob = (daily.get("precipitation_probability_max") or [None])[0]
    hourly = payload.get("hourly") or {}
    hourly_rows = []
    for idx, stamp in enumerate(hourly.get("time") or []):
        hourly_rows.append({
            "time": stamp[11:16],
            "weather": WMO_JA.get((hourly.get("weather_code") or [None] * (idx + 1))[idx], "不明"),
            "temperature": (hourly.get("temperature_2m") or [None] * (idx + 1))[idx],
            "precipitation_probability": (
                hourly.get("precipitation_probability") or [None] * (idx + 1)
            )[idx],
        })

    return {
        "ok": True,
        "location": place or settings.get("weather_location"),
        "date": target_date,
        "weather": WMO_JA.get(code, "不明"),
        "temperature_max": (daily.get("temperature_2m_max") or [None])[0],
        "temperature_min": (daily.get("temperature_2m_min") or [None])[0],
        "precipitation_probability": precip_prob,
        "precipitation_sum_mm": (daily.get("precipitation_sum") or [None])[0],
        "will_rain": bool(precip_prob is not None and precip_prob >= 50),
        "hourly": hourly_rows,
        "source": "open-meteo",
    }
