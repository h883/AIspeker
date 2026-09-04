"""経路案内（仕様書 9章）。Google Routes API v2 (computeRoutes) を使う。

APIキー未設定・取得失敗時は ok=False を返し、架空の経路を作らせない（仕様書 22/23章）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx

from backend import config, user_settings
from backend.tools.time import now, resolve_date, tz

COMPUTE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

FIELD_MASK = ",".join([
    "routes.duration",
    "routes.distanceMeters",
    "routes.legs.duration",
    "routes.legs.steps.travelMode",
    "routes.legs.steps.distanceMeters",
    "routes.legs.steps.staticDuration",
    "routes.legs.steps.navigationInstruction",
    "routes.legs.steps.transitDetails",
])

TRAVEL_MODES = {
    "transit": "TRANSIT",
    "walking": "WALK",
    "walk": "WALK",
    "bicycling": "BICYCLE",
    "bicycle": "BICYCLE",
    "driving": "DRIVE",
    "drive": "DRIVE",
    "電車": "TRANSIT",
    "徒歩": "WALK",
    "自転車": "BICYCLE",
    "車": "DRIVE",
}

PLACE_ALIASES_HOME = {"家", "自宅", "うち", "home", "現在地", "ここ"}
PLACE_ALIASES_SCHOOL = {"学校", "がっこう", "school", "大学", "専門学校"}


def _resolve_place(value: str | None, settings: dict[str, Any], fallback_key: str) -> str:
    text = (value or "").strip()
    if not text:
        return str(settings.get(fallback_key) or "")
    lowered = text.lower()
    if lowered in PLACE_ALIASES_HOME:
        return str(settings.get("home_address") or text)
    if lowered in PLACE_ALIASES_SCHOOL:
        return str(settings.get("school_address") or settings.get("school_name") or text)
    return text


def _parse_time(value: str | None) -> datetime | None:
    """ISO8601 や HH:MM、「明日 9:00」などを datetime へ変換する。"""
    if not value:
        return None
    text = value.strip().replace("　", " ")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz())
    except ValueError:
        pass
    date_part, _, time_part = text.rpartition(" ")
    clock = (time_part or text).replace("時", ":").replace("分", "").strip(": ")
    for fmt in ("%H:%M", "%H:%M:%S", "%H"):
        try:
            clock_dt = datetime.strptime(clock, fmt)
        except ValueError:
            continue
        day = datetime.fromisoformat(resolve_date(date_part or None)).date()
        return datetime.combine(day, clock_dt.time(), tzinfo=tz())
    return None


def _seconds(value: Any) -> int:
    """Routes API の 1234s 形式（末尾 s の文字列）を秒に変換する。"""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.endswith("s"):
        try:
            return int(float(value[:-1]))
        except ValueError:
            return 0
    return 0


def _format_steps(route: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            mode = step.get("travelMode", "")
            transit = step.get("transitDetails") or {}
            if transit:
                line = transit.get("transitLine") or {}
                stop_details = transit.get("stopDetails") or {}
                vehicle = line.get("vehicle") or {}
                steps.append({
                    "type": "transit",
                    "line": line.get("nameShort") or line.get("name") or "",
                    "vehicle": (vehicle.get("name") or {}).get("text", ""),
                    "from": (stop_details.get("departureStop") or {}).get("name", ""),
                    "to": (stop_details.get("arrivalStop") or {}).get("name", ""),
                    "departure": (stop_details.get("departureTime") or "")[11:16],
                    "arrival": (stop_details.get("arrivalTime") or "")[11:16],
                    "headsign": transit.get("headsign", ""),
                })
            else:
                instruction = (step.get("navigationInstruction") or {}).get("instructions", "")
                steps.append({
                    "type": mode.lower() or "walk",
                    "instruction": instruction,
                    "distance_m": step.get("distanceMeters", 0),
                    "duration_min": round(_seconds(step.get("staticDuration")) / 60),
                })
    return steps


def get_route(
    origin: str | None = None,
    destination: str | None = None,
    arrival_time: str | None = None,
    departure_time: str | None = None,
    travel_mode: str | None = None,
) -> dict[str, Any]:
    settings = user_settings.load()
    start = _resolve_place(origin, settings, "home_address")
    goal = _resolve_place(destination, settings, "school_address")

    if not start:
        return {"ok": False, "error": "出発地が設定されていません。設定画面で自宅住所を登録してください。"}
    if not goal:
        return {"ok": False, "error": "目的地が設定されていません。設定画面で学校の所在地を登録してください。"}
    if not config.GOOGLE_MAPS_API_KEY:
        return {
            "ok": False,
            "error": "経路APIのキーが未設定のため、経路情報を取得できませんでした。",
            "hint": ".env の GOOGLE_MAPS_API_KEY を設定してください。",
        }

    requested_mode = (travel_mode or settings.get("travel_mode") or "transit").lower()
    mode = TRAVEL_MODES.get(requested_mode, "TRANSIT")

    body: dict[str, Any] = {
        "origin": {"address": start},
        "destination": {"address": goal},
        "travelMode": mode,
        "languageCode": "ja",
        "units": "METRIC",
    }

    arrive_at = _parse_time(arrival_time)
    depart_at = _parse_time(departure_time)
    if mode == "TRANSIT":
        body["transitPreferences"] = {"routingPreference": "LESS_WALKING"}
        if arrive_at:
            body["arrivalTime"] = arrive_at.astimezone(tz()).isoformat()
        elif depart_at:
            body["departureTime"] = depart_at.astimezone(tz()).isoformat()
        else:
            body["departureTime"] = (now() + timedelta(minutes=1)).isoformat()
    else:
        if mode == "DRIVE":
            body["routingPreference"] = "TRAFFIC_AWARE"
        if depart_at:
            body["departureTime"] = depart_at.astimezone(tz()).isoformat()

    try:
        response = httpx.post(
            COMPUTE_ROUTES_URL,
            json=body,
            headers={
                "X-Goog-Api-Key": config.GOOGLE_MAPS_API_KEY,
                "X-Goog-FieldMask": FIELD_MASK,
                "Content-Type": "application/json",
            },
            timeout=20,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", {}).get("message", "")
            except ValueError:
                detail = response.text[:200]
            return {"ok": False, "error": f"経路情報を取得できませんでした（{detail}）。"}
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": f"経路情報を取得できませんでした（{exc.__class__.__name__}）。"}

    routes = payload.get("routes") or []
    if not routes:
        return {"ok": False, "error": "経路が見つかりませんでした。"}

    route = routes[0]
    duration_sec = _seconds(route.get("duration"))
    duration_min = max(1, round(duration_sec / 60))

    if arrive_at:
        arrival_dt = arrive_at
        departure_dt = arrive_at - timedelta(seconds=duration_sec)
    else:
        departure_dt = depart_at or now()
        arrival_dt = departure_dt + timedelta(seconds=duration_sec)

    return {
        "ok": True,
        "origin": start,
        "destination": goal,
        "travel_mode": mode,
        "duration_minutes": duration_min,
        "distance_km": round(route.get("distanceMeters", 0) / 1000, 1),
        "departure_time": departure_dt.strftime("%Y-%m-%d %H:%M"),
        "arrival_time": arrival_dt.strftime("%Y-%m-%d %H:%M"),
        "steps": _format_steps(route),
        "source": "google-routes-api",
    }
