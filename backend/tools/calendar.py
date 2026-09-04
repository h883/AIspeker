"""予定取得（仕様書 12章）。

Google Calendar を接続していればそちらを、していなければ SQLite のローカル予定表を使う。
どちらも失敗した場合は ok=False を返し、予定を推測させない（仕様書 22/23章）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend import config, user_settings
from backend.database import db
from backend.tools.time import now, resolve_date, tz

GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


# --- ローカル予定表 ---

def add_local_event(title: str, start: str, end: str = "", location: str = "") -> dict[str, Any]:
    event_id = db.execute(
        "INSERT INTO local_event (title, start, end, location, created_at) VALUES (?, ?, ?, ?, ?)",
        (title, start, end, location, now().isoformat(timespec="seconds")),
    )
    return {"ok": True, "id": event_id}


def delete_local_event(event_id: int) -> dict[str, Any]:
    db.execute("DELETE FROM local_event WHERE id = ?", (event_id,))
    return {"ok": True}


def _local_events(date_str: str, days: int) -> list[dict[str, Any]]:
    start = datetime.fromisoformat(date_str).date()
    end = start + timedelta(days=days)
    rows = db.query(
        "SELECT * FROM local_event WHERE date(start) >= ? AND date(start) < ? ORDER BY start",
        (start.isoformat(), end.isoformat()),
    )
    events = []
    for row in rows:
        events.append({
            "id": row["id"],
            "title": row["title"],
            "start": row["start"],
            "end": row["end"],
            "location": row["location"],
            "source": "local",
        })
    return events


# --- Google Calendar ---

def google_credentials():
    """保存済みトークンから Google 認証情報を返す。未接続なら None。"""
    token_path = Path(config.GOOGLE_OAUTH_TOKEN_FILE)
    if not token_path.exists():
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_SCOPES)
    except (ValueError, OSError):
        return None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        except Exception:  # noqa: BLE001 - 更新失敗はローカル予定表へフォールバック
            return None
    return creds if creds and creds.valid else None


def google_connected() -> bool:
    return google_credentials() is not None


def _google_events(date_str: str, days: int) -> list[dict[str, Any]] | None:
    creds = google_credentials()
    if creds is None:
        return None
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return None

    start = datetime.fromisoformat(date_str).replace(tzinfo=tz())
    end = start + timedelta(days=days)
    calendar_id = user_settings.get("google_calendar_id") or config.GOOGLE_CALENDAR_ID
    try:
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        response = service.events().list(
            calendarId=calendar_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()
    except Exception:  # noqa: BLE001 - 取得失敗は None を返して呼び出し側で処理
        return None

    events = []
    for item in response.get("items", []):
        start_field = item.get("start", {})
        end_field = item.get("end", {})
        events.append({
            "id": item.get("id", ""),
            "title": item.get("summary", "（無題）"),
            "start": start_field.get("dateTime") or start_field.get("date", ""),
            "end": end_field.get("dateTime") or end_field.get("date", ""),
            "location": item.get("location", ""),
            "all_day": "date" in start_field,
            "source": "google",
        })
    return events


# --- Tool 本体 ---

def get_calendar(date: str | None = None, days: int = 1) -> dict[str, Any]:
    target_date = resolve_date(date)
    days = max(1, min(int(days or 1), 14))
    source = user_settings.get("calendar_source", "local")

    events: list[dict[str, Any]] | None = None
    used = source
    if source == "google":
        events = _google_events(target_date, days)
        if events is None:
            # Google に繋がらないときは黙って作らず、ローカルを見に行ったことを明示する
            events = _local_events(target_date, days)
            used = "local(fallback)"
    else:
        events = _local_events(target_date, days)

    if events is None:
        return {"ok": False, "error": "予定を取得できませんでした。"}

    return {
        "ok": True,
        "date": target_date,
        "days": days,
        "count": len(events),
        "events": events,
        "source": used,
    }
