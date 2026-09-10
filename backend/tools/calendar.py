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
from backend.tools import timetable as timetable_tool
from backend.tools.reminder import parse_datetime
from backend.tools.time import now, resolve_date, tz

GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


# --- ローカル予定表 ---

def add_local_event(title: str, start: str, end: str = "", location: str = "") -> dict[str, Any]:
    event_id = db.execute(
        "INSERT INTO local_event (title, start, end, location, created_at) VALUES (?, ?, ?, ?, ?)",
        (title, start, end, location, now().isoformat(timespec="seconds")),
    )
    return {"ok": True, "id": event_id}


def _naive_local(value: datetime) -> str:
    """local_event に入れる形（タイムゾーンなしのローカル時刻）へ揃える。

    オフセット付きで保存すると、SQLite の date() が UTC へ換算してしまい、
    朝の予定が前日扱いになって範囲検索から漏れる。
    画面の datetime-local 入力もオフセットなしなので、そちらに合わせる。
    """
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def create_event(
    title: str = "",
    start: str = "",
    end: str = "",
    location: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """単発の予定を登録する（Tool Calling 用）。

    「明日15時」のような話し言葉も受け取れるようにする。
    解釈できなかったときは登録せず ok=False を返し、
    別の日時で登録されたと誤解させない（仕様書 22章）。
    """
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "予定の内容が指定されていません。"}

    raw_start = (start or kwargs.get("datetime") or kwargs.get("date") or "").strip()
    if not raw_start:
        return {"ok": False, "error": "予定の日時が指定されていません。"}

    start_dt = parse_datetime(raw_start)
    if start_dt is None:
        return {"ok": False, "error": f"予定の日時を解釈できませんでした（{raw_start}）。"}

    end_iso = ""
    raw_end = (end or "").strip()
    if raw_end:
        end_dt = parse_datetime(raw_end)
        if end_dt is None:
            return {"ok": False, "error": f"終了時刻を解釈できませんでした（{raw_end}）。"}
        end_iso = _naive_local(end_dt)

    result = add_local_event(
        title,
        _naive_local(start_dt),
        end_iso,
        (location or "").strip(),
    )
    return {
        "ok": True,
        "id": result["id"],
        "title": title,
        "start": start_dt.strftime("%Y-%m-%d %H:%M"),
        "end": end_iso[:16].replace("T", " "),
        "location": (location or "").strip(),
    }


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

def _timetable_events(date_str: str, days: int) -> list[dict[str, Any]]:
    """期間内の各日について、登録済みの時間割を予定の形で返す。"""
    if not user_settings.get("timetable_enabled", True):
        return []
    try:
        start = datetime.fromisoformat(date_str).date()
    except ValueError:
        return []
    events: list[dict[str, Any]] = []
    for offset in range(days):
        events.extend(timetable_tool.events_for_date((start + timedelta(days=offset)).isoformat()))
    return events


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

    # 登録済みの時間割を合成する。これにより Google カレンダーを繋がなくても
    # 「明日の最初の予定」から出発時刻を逆算できる（prompts.py の手順がそのまま動く）。
    lessons = _timetable_events(target_date, days)
    if lessons:
        events = sorted(events + lessons, key=lambda event: event.get("start", ""))
        used = f"{used}+timetable"

    return {
        "ok": True,
        "date": target_date,
        "days": days,
        "count": len(events),
        "events": events,
        "source": used,
    }
