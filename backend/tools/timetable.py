"""毎週繰り返す時間割。

単発の予定（local_event）と分けているのは、
「明日の1限は？」のような問いに曖昧さなく答えるため。
自由文の記憶（memory）に混ぜると取りこぼす。

Google カレンダーを繋がなくても予定機能が成立するよう、
ここで作った授業は get_calendar の結果へ合成される。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from backend import user_settings
from backend.database import db
from backend.tools.time import WEEKDAYS_JA, now, resolve_date, tz

# 「木」「木曜」「木曜日」「thu」いずれでも受け取れるようにする
_WEEKDAY_ALIASES: dict[str, int] = {}
for _index, _name in enumerate(WEEKDAYS_JA):
    for _form in (_name, f"{_name}曜", f"{_name}曜日"):
        _WEEKDAY_ALIASES[_form] = _index
for _index, _name in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"]):
    _WEEKDAY_ALIASES[_name] = _index
    _WEEKDAY_ALIASES[_name + "day"] = _index
_WEEKDAY_ALIASES.update({
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
})


def parse_weekday(value: Any) -> int | None:
    """曜日の指定を 0（月）〜6（日）へ。解釈できなければ None。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 6 else None
    text = str(value).strip().lower()
    if text.isdigit():
        number = int(text)
        return number if 0 <= number <= 6 else None
    return _WEEKDAY_ALIASES.get(text)


def _period_times(period: int) -> tuple[str, str]:
    """時限から開始・終了時刻を計算する。

    個別に start_time が登録されていない授業のための既定値。
    授業開始時刻・1コマの長さ・休み時間はユーザー設定から取る。
    """
    settings = user_settings.load()
    try:
        base = datetime.strptime(str(settings.get("school_start_time") or "09:00"), "%H:%M")
    except ValueError:
        base = datetime.strptime("09:00", "%H:%M")
    length = int(settings.get("period_minutes") or 50)
    rest = int(settings.get("break_minutes") or 10)

    start = base + timedelta(minutes=(period - 1) * (length + rest))
    end = start + timedelta(minutes=length)
    return start.strftime("%H:%M"), end.strftime("%H:%M")


def _public(row: dict[str, Any]) -> dict[str, Any]:
    start = row["start_time"] or _period_times(row["period"])[0]
    end = row["end_time"] or _period_times(row["period"])[1]
    return {
        "id": row["id"],
        "weekday": row["weekday"],
        "weekday_label": WEEKDAYS_JA[row["weekday"]],
        "period": row["period"],
        "subject": row["subject"],
        "teacher": row["teacher"],
        "room": row["room"],
        "start_time": start,
        "end_time": end,
    }


# --- Tool Calling から呼ばれる ---

def set_timetable(
    weekday: Any = None,
    period: Any = None,
    subject: str = "",
    teacher: str = "",
    room: str = "",
    start_time: str = "",
    end_time: str = "",
) -> dict[str, Any]:
    """時間割にひとコマ登録する。同じ曜日・時限なら上書きする。"""
    day = parse_weekday(weekday)
    if day is None:
        return {"ok": False, "error": f"曜日を解釈できませんでした（{weekday}）。"}
    try:
        slot = int(period)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"時限を解釈できませんでした（{period}）。"}
    if not 1 <= slot <= 12:
        return {"ok": False, "error": "時限は1〜12で指定してください。"}
    subject = (subject or "").strip()
    if not subject:
        return {"ok": False, "error": "科目名が指定されていません。"}

    stamp = now().isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO timetable"
        " (weekday, period, subject, teacher, room, start_time, end_time, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(weekday, period) DO UPDATE SET"
        "   subject = excluded.subject,"
        "   teacher = excluded.teacher,"
        "   room = excluded.room,"
        "   start_time = excluded.start_time,"
        "   end_time = excluded.end_time,"
        "   updated_at = excluded.updated_at",
        (day, slot, subject, (teacher or "").strip(), (room or "").strip(),
         (start_time or "").strip(), (end_time or "").strip(), stamp, stamp),
    )
    rows = db.query(
        "SELECT * FROM timetable WHERE weekday = ? AND period = ?", (day, slot)
    )
    return {"ok": True, **_public(rows[0])}


def get_timetable(weekday: Any = None, date: str | None = None) -> dict[str, Any]:
    """時間割を取得する。曜日か日付を指定するとその日の分だけ返す。"""
    day = parse_weekday(weekday)
    target_date = ""
    if day is None and date:
        target_date = resolve_date(date)
        day = datetime.fromisoformat(target_date).weekday()

    if day is None:
        rows = db.query("SELECT * FROM timetable ORDER BY weekday, period")
    else:
        rows = db.query(
            "SELECT * FROM timetable WHERE weekday = ? ORDER BY period", (day,)
        )

    lessons = [_public(row) for row in rows]
    return {
        "ok": True,
        "weekday": day,
        "weekday_label": WEEKDAYS_JA[day] if day is not None else "",
        "date": target_date,
        "count": len(lessons),
        "lessons": lessons,
        "note": "登録済みの平常時の時間割。休講・祝日は反映されない。",
    }


def clear_timetable(weekday: Any = None, period: Any = None) -> dict[str, Any]:
    """時間割からひとコマ消す。"""
    day = parse_weekday(weekday)
    if day is None:
        return {"ok": False, "error": f"曜日を解釈できませんでした（{weekday}）。"}
    try:
        slot = int(period)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"時限を解釈できませんでした（{period}）。"}
    db.execute("DELETE FROM timetable WHERE weekday = ? AND period = ?", (day, slot))
    return {"ok": True, "weekday": day, "period": slot}


# --- カレンダーへの合成 ---

def events_for_date(date_str: str) -> list[dict[str, Any]]:
    """その日の時間割を、予定と同じ形にして返す。

    source を timetable にしておき、確定した予定ではなく
    「登録した時間割から出したもの」だと分かるようにする。
    """
    try:
        day = datetime.fromisoformat(date_str).date()
    except ValueError:
        return []

    rows = db.query(
        "SELECT * FROM timetable WHERE weekday = ? ORDER BY period", (day.weekday(),)
    )
    events = []
    for row in rows:
        lesson = _public(row)
        start = datetime.combine(
            day, datetime.strptime(lesson["start_time"], "%H:%M").time(), tzinfo=tz()
        )
        end = datetime.combine(
            day, datetime.strptime(lesson["end_time"], "%H:%M").time(), tzinfo=tz()
        )
        events.append({
            "id": f"timetable-{row['id']}",
            "title": f"{lesson['period']}限 {lesson['subject']}",
            # local_event と同じ「タイムゾーンなしのローカル時刻」で揃える。
            # 混ぜて並べ替えるので表記が違うと順序が狂う。
            "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "location": lesson["room"],
            "teacher": lesson["teacher"],
            "period": lesson["period"],
            "source": "timetable",
        })
    return events


# --- 画面（REST）から呼ばれる ---

def list_timetable() -> dict[str, Any]:
    rows = db.query("SELECT * FROM timetable ORDER BY weekday, period")
    return {
        "ok": True,
        "weekdays": WEEKDAYS_JA,
        "count": len(rows),
        "lessons": [_public(row) for row in rows],
    }


def delete_timetable(lesson_id: int) -> dict[str, Any]:
    db.execute("DELETE FROM timetable WHERE id = ?", (lesson_id,))
    return {"ok": True}
