"""Gemini から呼び出せるツールの宣言と実行（仕様書 8章）。

宣言は Gemini の function calling スキーマ（OpenAPI サブセット）に合わせる。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from backend import user_settings
from backend.tools import calendar as calendar_tool
from backend.tools import memory as memory_tool
from backend.tools import reminder as reminder_tool
from backend.tools import routes as routes_tool
from backend.tools import time as time_tool
from backend.tools import timetable as timetable_tool
from backend.tools import weather as weather_tool

logger = logging.getLogger(__name__)


def get_user_profile() -> dict[str, Any]:
    """住所そのものは返さず、判断に必要な設定値だけを Gemini へ渡す。"""
    settings = user_settings.load()
    return {
        "ok": True,
        "user_name": settings.get("user_name"),
        "school_name": settings.get("school_name"),
        "school_start_time": settings.get("school_start_time"),
        "travel_mode": settings.get("travel_mode"),
        "buffer_minutes": settings.get("buffer_minutes"),
        "rain_extra_minutes": settings.get("rain_extra_minutes"),
        "weather_location": settings.get("weather_location"),
        "home_registered": bool(settings.get("home_address")),
        "school_registered": bool(settings.get("school_address")),
    }


TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "get_current_time",
        "description": "現在の日付・時刻・曜日を取得する。時刻に関する判断が必要なときは必ず最初に呼ぶこと。",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "get_user_profile",
        "description": "ユーザーの学校名・授業開始時刻・移動手段・余裕時間などの登録設定を取得する。",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name": "get_calendar",
        "description": "指定日の予定を取得する。今日/明日/今週の予定を聞かれたときに使う。",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date": {
                    "type": "STRING",
                    "description": "対象日。YYYY-MM-DD、または 今日 / 明日 / 明後日。省略時は今日。",
                },
                "days": {
                    "type": "INTEGER",
                    "description": "対象日から何日分取得するか。今週なら 7。省略時は 1。",
                },
            },
        },
    },
    {
        "name": "get_route",
        "description": (
            "出発地から目的地までの経路と所要時間を取得する。"
            "到着時刻を指定すると、その時刻に着くための出発時刻を返す。"
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin": {"type": "STRING", "description": "出発地。省略時は登録済みの自宅。"},
                "destination": {"type": "STRING", "description": "目的地。省略時は登録済みの学校。"},
                "arrival_time": {
                    "type": "STRING",
                    "description": "到着したい時刻。YYYY-MM-DDTHH:MM または HH:MM。",
                },
                "departure_time": {
                    "type": "STRING",
                    "description": "出発予定時刻。YYYY-MM-DDTHH:MM または HH:MM。",
                },
                "travel_mode": {
                    "type": "STRING",
                    "description": "移動手段。transit / walking / bicycling / driving。省略時は登録設定。",
                },
            },
        },
    },
    {
        "name": "get_weather",
        "description": "指定地域・指定日の天気、気温、降水確率を取得する。傘の要否を答えるときにも使う。",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "location": {"type": "STRING", "description": "地名。省略時は登録済みの地域。"},
                "date": {
                    "type": "STRING",
                    "description": "対象日。YYYY-MM-DD、または 今日 / 明日。省略時は今日。",
                },
            },
        },
    },
    {
        "name": "create_reminder",
        "description": "リマインダーを登録する。「20分後に教えて」のような相対時刻も指定できる。",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "リマインダーの内容。例: 洗濯物"},
                "datetime": {
                    "type": "STRING",
                    "description": "通知日時。YYYY-MM-DDTHH:MM、HH:MM、または 20分後 のような相対指定。",
                },
                "message": {"type": "STRING", "description": "読み上げるメッセージ。省略可。"},
            },
            "required": ["title", "datetime"],
        },
    },
    {
        "name": "get_reminders",
        "description": "登録済みで未通知のリマインダー一覧を取得する。",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "include_done": {"type": "BOOLEAN", "description": "通知済みも含めるか。省略時は false。"},
            },
        },
    },
    {
        "name": "create_event",
        "description": (
            "単発の予定をカレンダーに登録する。"
            "「明日15時に歯医者」のように日時と内容を言われたときに使う。"
            "毎週繰り返す授業は set_timetable を使うこと。"
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {"type": "STRING", "description": "予定の内容。例: 歯医者"},
                "start": {
                    "type": "STRING",
                    "description": "開始日時。YYYY-MM-DDTHH:MM、HH:MM、または 明日 15:00 のような指定。",
                },
                "end": {"type": "STRING", "description": "終了日時。省略可。"},
                "location": {"type": "STRING", "description": "場所。省略可。"},
            },
            "required": ["title", "start"],
        },
    },
    {
        "name": "get_timetable",
        "description": (
            "登録済みの時間割を取得する。「明日の1限は？」「木曜の授業は？」に答えるときに使う。"
            "曜日も日付も省略すると1週間分をすべて返す。"
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "weekday": {"type": "STRING", "description": "曜日。月 / 火 / ... または 月曜。"},
                "date": {"type": "STRING", "description": "対象日。YYYY-MM-DD、今日、明日など。"},
            },
        },
    },
    {
        "name": "set_timetable",
        "description": (
            "毎週繰り返す授業を時間割に登録する。同じ曜日・時限は上書きされる。"
            "「木曜の3限は情報処理」のように言われたときに使う。"
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "weekday": {"type": "STRING", "description": "曜日。月 / 火 / ... または 月曜。"},
                "period": {"type": "INTEGER", "description": "時限。1限なら 1。"},
                "subject": {"type": "STRING", "description": "科目名。例: 情報処理"},
                "teacher": {"type": "STRING", "description": "担当の先生。省略可。"},
                "room": {"type": "STRING", "description": "教室。省略可。"},
            },
            "required": ["weekday", "period", "subject"],
        },
    },
    {
        "name": "remember",
        "description": (
            "ユーザーについて覚えておくべき事実を記憶する。"
            "「覚えておいて」と言われたとき、また次回以降も役立つ情報を聞いたときに使う。"
            "同じ key で呼ぶと上書きになるので、覚え直しにも使える。"
            "予定や授業は create_event / set_timetable を使い、ここには入れないこと。"
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "key": {"type": "STRING", "description": "覚える項目の名前。例: 情報処理の担当"},
                "value": {"type": "STRING", "description": "覚える内容。例: 山田先生"},
                "category": {
                    "type": "STRING",
                    "description": "分類。teacher / assignment / club / exam / school / preference / general。",
                },
                "expires": {
                    "type": "STRING",
                    "description": "覚えておく期限。YYYY-MM-DD、明日、来週など。提出物のように期限があるときだけ指定する。",
                },
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "recall",
        "description": (
            "覚えている事実を検索する。過去に登録した内容を確認したいときに使う。"
            "主要な記憶は最初から渡されているので、通常は呼ばなくてよい。"
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "検索語。省略すると全件返す。"},
                "category": {"type": "STRING", "description": "分類で絞り込む。省略可。"},
            },
        },
    },
    {
        "name": "forget",
        "description": "覚えている事実を削除する。「もう覚えなくていい」と言われたときに使う。",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "key": {"type": "STRING", "description": "消す項目の名前。"},
            },
            "required": ["key"],
        },
    },
]


HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_current_time": time_tool.get_current_time,
    "get_user_profile": get_user_profile,
    "get_calendar": calendar_tool.get_calendar,
    "get_route": routes_tool.get_route,
    "get_weather": weather_tool.get_weather,
    "create_reminder": reminder_tool.create_reminder,
    "get_reminders": reminder_tool.get_reminders,
    "create_event": calendar_tool.create_event,
    "get_timetable": timetable_tool.get_timetable,
    "set_timetable": timetable_tool.set_timetable,
    "remember": memory_tool.remember,
    "recall": memory_tool.recall,
    "forget": memory_tool.forget,
}


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"未知のツール: {name}"}
    try:
        logger.info("tool call: %s %s", name, args)
        result = handler(**(args or {}))
    except TypeError as exc:
        return {"ok": False, "error": f"ツール引数が不正です: {exc}"}
    except Exception as exc:  # noqa: BLE001 - ツール障害は AI に伝えて謝罪させる
        logger.exception("tool failed: %s", name)
        return {"ok": False, "error": f"{name} の実行に失敗しました（{exc.__class__.__name__}）。"}
    if not isinstance(result, dict):
        return {"ok": True, "result": result}
    return result
