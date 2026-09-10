"""ホーム画面・各タブ向けのデータ API（仕様書 14章・16章）。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.tools import calendar as calendar_tool
from backend.tools import memory as memory_tool
from backend.tools import reminder as reminder_tool
from backend.tools import routes as routes_tool
from backend.tools import time as time_tool
from backend.tools import timetable as timetable_tool
from backend.tools import weather as weather_tool

router = APIRouter(prefix="/api", tags=["dashboard"])


class LocalEvent(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start: str
    end: str = ""
    location: str = ""


class ReminderCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    datetime: str
    message: str = ""


class MemoryCreate(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=1000)
    category: str = "general"
    expires: str = ""


class LessonCreate(BaseModel):
    weekday: int = Field(ge=0, le=6)
    period: int = Field(ge=1, le=12)
    subject: str = Field(min_length=1, max_length=100)
    teacher: str = ""
    room: str = ""


@router.get("/home")
def home() -> dict:
    """時計・天気・次の予定・学校までの所要時間をまとめて返す。"""
    now_info = time_tool.get_current_time()
    weather = weather_tool.get_weather()
    # 「次の予定」は今日で終わらないので数日先まで見る
    calendar = calendar_tool.get_calendar(days=3)

    next_event = None
    if calendar.get("ok"):
        today = now_info["date"]
        now_iso = now_info["iso"][:16]
        for event in calendar["events"]:
            start = event.get("start", "")
            is_upcoming = start[:10] >= today if len(start) == 10 else start[:16] >= now_iso
            if is_upcoming:
                next_event = event
                break

    route = routes_tool.get_route()

    return {
        "ok": True,
        "time": now_info,
        "weather": weather,
        "calendar": calendar,
        "next_event": next_event,
        "route": route,
        "reminders": reminder_tool.get_reminders()["reminders"],
    }


@router.get("/calendar")
def calendar(date: str | None = None, days: int = 7) -> dict:
    return calendar_tool.get_calendar(date=date, days=days)


@router.post("/calendar/local")
def create_local_event(event: LocalEvent) -> dict:
    return calendar_tool.add_local_event(event.title, event.start, event.end, event.location)


@router.delete("/calendar/local/{event_id}")
def remove_local_event(event_id: int) -> dict:
    return calendar_tool.delete_local_event(event_id)


@router.get("/weather")
def weather(location: str | None = None, date: str | None = None) -> dict:
    return weather_tool.get_weather(location=location, date=date)


@router.get("/route")
def route(
    origin: str | None = None,
    destination: str | None = None,
    arrival_time: str | None = None,
    departure_time: str | None = None,
    travel_mode: str | None = None,
) -> dict:
    return routes_tool.get_route(
        origin=origin,
        destination=destination,
        arrival_time=arrival_time,
        departure_time=departure_time,
        travel_mode=travel_mode,
    )


@router.get("/reminders")
def reminders(include_done: bool = False) -> dict:
    return reminder_tool.get_reminders(include_done=include_done)


@router.post("/reminders")
def create_reminder(payload: ReminderCreate) -> dict:
    return reminder_tool.create_reminder(
        title=payload.title, datetime=payload.datetime, message=payload.message
    )


@router.delete("/reminders/{reminder_id}")
def delete_reminder(reminder_id: int) -> dict:
    return reminder_tool.delete_reminder(reminder_id)


@router.get("/reminders/due")
def due_reminders() -> dict:
    """時刻が来たリマインダーを取り出す。フロントが定期的にポーリングする。"""
    return {"ok": True, "due": reminder_tool.pop_due_reminders()}


# --- 記憶（設定画面から中身を確認・訂正できるようにする）---

@router.get("/memory")
def memories(include_expired: bool = False) -> dict:
    return memory_tool.list_memories(include_expired=include_expired)


@router.post("/memory")
def create_memory(payload: MemoryCreate) -> dict:
    """画面からの登録。AI が覚えたものと区別するため source は manual にする。"""
    return memory_tool.remember(
        key=payload.key,
        value=payload.value,
        category=payload.category,
        expires=payload.expires,
        source="manual",
    )


@router.delete("/memory/{memory_id}")
def remove_memory(memory_id: int) -> dict:
    return memory_tool.delete_memory(memory_id)


@router.delete("/memory")
def clear_memory() -> dict:
    return memory_tool.clear_memories()


# --- 時間割 ---

@router.get("/timetable")
def timetable() -> dict:
    return timetable_tool.list_timetable()


@router.post("/timetable")
def create_lesson(payload: LessonCreate) -> dict:
    return timetable_tool.set_timetable(
        weekday=payload.weekday,
        period=payload.period,
        subject=payload.subject,
        teacher=payload.teacher,
        room=payload.room,
    )


@router.delete("/timetable/{lesson_id}")
def remove_lesson(lesson_id: int) -> dict:
    return timetable_tool.delete_timetable(lesson_id)
