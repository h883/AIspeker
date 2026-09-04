"""リマインダー（仕様書 19章）。

「20分後に洗濯物教えて」のような相対時刻も解釈する。
到来したリマインダーは /api/reminders/due をフロントがポーリングして通知する。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from backend.database import db
from backend.tools.time import now, resolve_date, tz

RELATIVE_RE = re.compile(r"^\s*(\d+)\s*(分|分後|minutes?|min|時間|時間後|hours?|h|秒|seconds?|s)\s*(後|later)?\s*$")


def parse_datetime(value: str) -> datetime | None:
    """ISO8601 / HH:MM / 「20分後」/「明日 7:30」を datetime へ。"""
    if not value:
        return None
    text = value.strip().replace("　", " ")

    match = RELATIVE_RE.match(text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit.startswith(("分", "min")):
            return now() + timedelta(minutes=amount)
        if unit.startswith(("時間", "hour", "h")):
            return now() + timedelta(hours=amount)
        return now() + timedelta(seconds=amount)

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
        candidate = datetime.combine(day, clock_dt.time(), tzinfo=tz())
        if not date_part and candidate < now():
            candidate += timedelta(days=1)
        return candidate
    return None


def create_reminder(title: str, datetime_: str = "", message: str = "", **kwargs: Any) -> dict[str, Any]:
    # Gemini からは datetime という引数名で来るため両方受ける
    raw = datetime_ or kwargs.get("datetime") or ""
    when = parse_datetime(raw)
    if when is None:
        return {"ok": False, "error": f"リマインダーの日時を解釈できませんでした（{raw}）。"}
    if not title:
        return {"ok": False, "error": "リマインダーの内容が指定されていません。"}

    reminder_id = db.execute(
        "INSERT INTO reminder (title, datetime, message, notified, created_at) VALUES (?, ?, ?, 0, ?)",
        (title, when.isoformat(timespec="seconds"), message, now().isoformat(timespec="seconds")),
    )
    return {
        "ok": True,
        "id": reminder_id,
        "title": title,
        "datetime": when.strftime("%Y-%m-%d %H:%M"),
        "message": message,
    }


def get_reminders(include_done: bool = False) -> dict[str, Any]:
    if include_done:
        rows = db.query("SELECT * FROM reminder ORDER BY datetime")
    else:
        rows = db.query("SELECT * FROM reminder WHERE notified = 0 ORDER BY datetime")
    return {
        "ok": True,
        "count": len(rows),
        "reminders": [
            {
                "id": row["id"],
                "title": row["title"],
                "datetime": row["datetime"][:16].replace("T", " "),
                "message": row["message"],
                "done": bool(row["notified"]),
            }
            for row in rows
        ],
    }


def delete_reminder(reminder_id: int) -> dict[str, Any]:
    db.execute("DELETE FROM reminder WHERE id = ?", (reminder_id,))
    return {"ok": True}


def pop_due_reminders() -> list[dict[str, Any]]:
    """時刻が来た未通知リマインダーを返し、通知済みにする。"""
    current = now().isoformat(timespec="seconds")
    rows = db.query(
        "SELECT * FROM reminder WHERE notified = 0 AND datetime <= ? ORDER BY datetime",
        (current,),
    )
    due = []
    for row in rows:
        db.execute("UPDATE reminder SET notified = 1 WHERE id = ?", (row["id"],))
        due.append({
            "id": row["id"],
            "title": row["title"],
            "message": row["message"] or f"{row['title']}の時間です。",
            "datetime": row["datetime"][:16].replace("T", " "),
        })
    return due
