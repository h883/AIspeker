"""標準ライブラリだけで動くテスト。

  python -m unittest discover -s tests

Gemini や外部 API は呼ばず、ツール解釈と Tool Calling ループの組み立てを検証する。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 本番のデータベース・設定に触らないよう、読み込み前に差し替える
_TMP = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(_TMP.name) / "test.db")
os.environ["SETTINGS_PATH"] = str(Path(_TMP.name) / "settings.json")
os.environ["ENV_PATH"] = str(Path(_TMP.name) / ".env")   # 本物の .env を触らない
os.environ["GEMINI_API_KEY"] = "test-key"

from backend import config  # noqa: E402
from backend.ai import gemini  # noqa: E402
from backend.database import db  # noqa: E402
from backend.tools import calendar as calendar_tool  # noqa: E402
from backend.tools import memory as memory_tool  # noqa: E402
from backend.tools import registry  # noqa: E402
from backend.tools import reminder as reminder_tool  # noqa: E402
from backend.tools import routes as routes_tool  # noqa: E402
from backend.tools import time as time_tool  # noqa: E402
from backend.tools import timetable as timetable_tool  # noqa: E402
from backend.tools import weather as weather_tool  # noqa: E402
from backend import user_settings  # noqa: E402


class DateParsingTest(unittest.TestCase):
    def test_relative_keywords(self) -> None:
        today = time_tool.now().date()
        self.assertEqual(time_tool.resolve_date("今日"), today.isoformat())
        self.assertEqual(time_tool.resolve_date("明日"), (today + timedelta(days=1)).isoformat())
        self.assertEqual(time_tool.resolve_date("あさって"), (today + timedelta(days=2)).isoformat())

    def test_explicit_date(self) -> None:
        self.assertEqual(time_tool.resolve_date("2026-09-05"), "2026-09-05")
        self.assertEqual(time_tool.resolve_date("2026/09/05"), "2026-09-05")

    def test_unparsable_falls_back_to_today(self) -> None:
        self.assertEqual(time_tool.resolve_date("いつか"), time_tool.now().date().isoformat())

    def test_route_time_parsing(self) -> None:
        parsed = routes_tool._parse_time("09:00")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.hour, 9)
        self.assertEqual(parsed.minute, 0)


class ReminderTest(unittest.TestCase):
    def setUp(self) -> None:
        db.init_db()
        db.execute("DELETE FROM reminder")

    def test_relative_minutes(self) -> None:
        result = reminder_tool.create_reminder(title="洗濯物", datetime="20分後")
        self.assertTrue(result["ok"])
        expected = (time_tool.now() + timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(result["datetime"], expected)

    def test_unparsable_time_is_rejected(self) -> None:
        result = reminder_tool.create_reminder(title="何か", datetime="そのうち")
        self.assertFalse(result["ok"])
        self.assertIn("解釈できません", result["error"])

    def test_due_reminders_are_popped_once(self) -> None:
        reminder_tool.create_reminder(title="今すぐ", datetime="1秒後")
        past = (time_tool.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
        db.execute("UPDATE reminder SET datetime = ? WHERE title = ?", (past, "今すぐ"))

        self.assertEqual(len(reminder_tool.pop_due_reminders()), 1)
        self.assertEqual(len(reminder_tool.pop_due_reminders()), 0)


class RouteFallbackTest(unittest.TestCase):
    def test_missing_api_key_does_not_invent_a_route(self) -> None:
        """仕様書 22章: 取得できないときに架空の経路を作らない。"""
        with mock.patch.object(routes_tool.config, "GOOGLE_MAPS_API_KEY", ""), \
             mock.patch.object(routes_tool.user_settings, "load", return_value={
                 "home_address": "大阪市北区", "school_address": "大阪市西区", "travel_mode": "transit",
             }):
            result = routes_tool.get_route()
        self.assertFalse(result["ok"])
        self.assertIn("取得できませんでした", result["error"])


class ToolRegistryTest(unittest.TestCase):
    def test_every_declaration_has_a_handler(self) -> None:
        declared = {tool["name"] for tool in registry.TOOL_DECLARATIONS}
        self.assertEqual(declared, set(registry.HANDLERS))

    def test_unknown_tool_is_reported(self) -> None:
        result = registry.call_tool("get_horoscope", {})
        self.assertFalse(result["ok"])

    def test_handler_exception_becomes_error_result(self) -> None:
        with mock.patch.dict(registry.HANDLERS, {"boom": mock.Mock(side_effect=RuntimeError("x"))}):
            result = registry.call_tool("boom", {})
        self.assertFalse(result["ok"])


class EnvUpdateTest(unittest.TestCase):
    """セットアップ画面からの .env 書き換え（再起動なしで反映されること）。"""

    def setUp(self) -> None:
        config.ENV_PATH.write_text(
            "# コメントは残す\nGEMINI_API_KEY=old-key\nPORT=8000\n", encoding="utf-8"
        )

    def test_updates_value_and_keeps_comments(self) -> None:
        saved = config.update_env({"GEMINI_API_KEY": "new-key"})
        self.assertEqual(saved, ["GEMINI_API_KEY"])

        text = config.ENV_PATH.read_text(encoding="utf-8")
        self.assertIn("# コメントは残す", text)
        self.assertIn("GEMINI_API_KEY=new-key", text)
        self.assertIn("PORT=8000", text)
        # 再起動なしでモジュール側にも反映される
        self.assertEqual(config.GEMINI_API_KEY, "new-key")

    def test_appends_missing_key(self) -> None:
        config.update_env({"GOOGLE_MAPS_API_KEY": "maps-key"})
        self.assertIn("GOOGLE_MAPS_API_KEY=maps-key", config.ENV_PATH.read_text(encoding="utf-8"))
        self.assertEqual(config.GOOGLE_MAPS_API_KEY, "maps-key")

    def test_blank_value_does_not_erase(self) -> None:
        """空欄のまま保存しても、登録済みの鍵を消さない。"""
        saved = config.update_env({"GEMINI_API_KEY": "   "})
        self.assertEqual(saved, [])
        self.assertIn("GEMINI_API_KEY=old-key", config.ENV_PATH.read_text(encoding="utf-8"))

    def test_unknown_key_is_ignored(self) -> None:
        config.update_env({"SSL_KEY_FILE": "/etc/passwd", "PATH": "/tmp"})
        text = config.ENV_PATH.read_text(encoding="utf-8")
        self.assertNotIn("SSL_KEY_FILE", text)
        self.assertNotIn("PATH=", text)


class ModelListingTest(unittest.TestCase):
    """使えないモデルを選ばせて後から 404 にならないようにする仕掛け。"""

    LISTING = {
        "models": [
            {"name": "models/gemini-2.5-flash", "displayName": "Flash 2.5",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.6-pro", "displayName": "Pro 3.6",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.6-flash", "displayName": "Flash 3.6",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004",
             "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/imagen-3.0", "supportedGenerationMethods": ["predict"]},
        ]
    }

    def _ok(self, payload: dict):
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = payload
        return response

    def test_only_chat_models_are_offered(self) -> None:
        with mock.patch.object(gemini.httpx, "get", return_value=self._ok(self.LISTING)):
            result = gemini.list_models("some-key")
        names = [item["name"] for item in result["models"]]
        self.assertEqual(names, ["gemini-3.6-flash", "gemini-3.6-pro", "gemini-2.5-flash"])
        self.assertEqual(result["recommended"], "gemini-3.6-flash")

    def test_newer_versions_rank_first(self) -> None:
        """古いモデルは新規ユーザーに提供終了することがあるので、新しい順に試す。"""
        names = ["gemini-2.5-flash", "gemini-3.6-pro", "gemini-3.6-flash",
                 "gemini-3.6-flash-preview", "gemini-2.0-flash"]
        self.assertEqual(
            sorted(names, key=gemini._model_rank),
            ["gemini-3.6-flash", "gemini-3.6-pro", "gemini-2.5-flash",
             "gemini-2.0-flash", "gemini-3.6-flash-preview"],
        )

    def test_retired_model_falls_through_to_the_next(self) -> None:
        """一覧には出るが応答しないモデルは飛ばして、次の候補を採用する。"""
        retired = mock.Mock()
        retired.status_code = 404
        retired.json.return_value = {"error": {"message":
            "This model models/gemini-2.5-flash is no longer available to new users."}}
        working = mock.Mock()
        working.status_code = 200

        with mock.patch.object(gemini.httpx, "get", return_value=self._ok(self.LISTING)), \
             mock.patch.object(gemini.httpx, "post", side_effect=[retired, working]):
            result = gemini.verify_key("some-key", "gemini-2.5-flash")

        self.assertTrue(result["ok"])
        self.assertTrue(result["switched"])
        self.assertEqual(result["requested"], "gemini-2.5-flash")
        self.assertEqual(result["model"], "gemini-3.6-flash")

    def test_denied_project_is_not_blamed_on_the_key(self) -> None:
        """キーは有効でもプロジェクトが拒否されることがある。案内を取り違えない。"""
        denied = mock.Mock()
        denied.status_code = 403
        denied.json.return_value = {"error": {"message":
            "Your project has been denied access. Please contact support."}}

        with mock.patch.object(gemini.httpx, "get", return_value=self._ok(self.LISTING)), \
             mock.patch.object(gemini.httpx, "post", return_value=denied) as post:
            result = gemini.verify_key("valid-key", "gemini-3.6-flash")

        self.assertFalse(result["ok"])
        self.assertIn("APIキー自体は有効", result["error"])
        self.assertIn("プロジェクト", result["error"])
        # モデルを変えても直らないので、総当たりしない
        self.assertEqual(post.call_count, 1)

    def test_error_messages_are_actionable(self) -> None:
        cases = [
            (403, "Your project has been denied access.", "プロジェクト"),
            (403, "API key not valid. Please pass a valid API key.", "APIキーが正しくありません"),
            (403, "Generative Language API has not been used in project 123 before", "有効になっていません"),
            (400, "User location is not supported for the API use.", "地域"),
            (429, "Quota exceeded", "利用制限"),
        ]
        for status, raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertIn(expected, gemini.explain_error(status, raw))

    def test_bad_key_is_reported_before_choosing_a_model(self) -> None:
        denied = mock.Mock()
        denied.status_code = 403
        denied.json.return_value = {"error": {"message": "API key not valid"}}
        with mock.patch.object(gemini.httpx, "get", return_value=denied):
            result = gemini.verify_key("bad-key", "gemini-3.6-flash")
        self.assertFalse(result["ok"])
        self.assertIn("APIキー", result["error"])

    def test_rate_limit_does_not_burn_through_every_model(self) -> None:
        limited = mock.Mock()
        limited.status_code = 429
        limited.json.return_value = {"error": {"message": "quota"}}
        with mock.patch.object(gemini.httpx, "get", return_value=self._ok(self.LISTING)), \
             mock.patch.object(gemini.httpx, "post", return_value=limited) as post:
            result = gemini.verify_key("some-key", "gemini-3.6-flash")
        self.assertFalse(result["ok"])
        self.assertIn("利用制限", result["error"])
        self.assertEqual(post.call_count, 1)

    def test_usable_model_passes(self) -> None:
        generate = mock.Mock()
        generate.status_code = 200
        with mock.patch.object(gemini.httpx, "get", return_value=self._ok(self.LISTING)), \
             mock.patch.object(gemini.httpx, "post", return_value=generate):
            result = gemini.verify_key("some-key", "gemini-3.6-flash")
        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "gemini-3.6-flash")
        self.assertFalse(result["switched"])


class AssetVersionTest(unittest.TestCase):
    """更新したのにブラウザが古い画面を出し続ける事故を防ぐ仕掛け。"""

    def test_placeholder_is_replaced(self) -> None:
        from backend import main

        html = main.render_index().body.decode("utf-8")
        self.assertNotIn("__ASSET_VERSION__", html)
        self.assertIn(f"/js/app.js?v={main.asset_version()}", html)

    def test_index_is_never_cached(self) -> None:
        from backend import main

        self.assertEqual(main.render_index().headers["Cache-Control"], "no-store")

    def test_version_changes_when_a_file_changes(self) -> None:
        from backend import main

        before = main.asset_version()
        app_js = main.config.FRONTEND_DIR / "js" / "app.js"
        original = app_js.stat().st_mtime
        try:
            os.utime(app_js, (original + 60, original + 60))
            self.assertNotEqual(main.asset_version(), before)
        finally:
            os.utime(app_js, (original, original))
        self.assertEqual(main.asset_version(), before)


class GeocodeCandidateTest(unittest.TestCase):
    """Open-Meteo は市名しか引けないので、区名などから候補を作れているか。"""

    def test_ward_falls_back_to_city(self) -> None:
        self.assertIn("大阪市", weather_tool.geocode_candidates("大阪市天王寺区"))

    def test_prefecture_prefix_is_stripped(self) -> None:
        candidates = weather_tool.geocode_candidates("東京都新宿区")
        self.assertIn("新宿区", candidates)
        self.assertIn("新宿", candidates)

    def test_bare_name_gets_city_suffix(self) -> None:
        self.assertIn("大阪市", weather_tool.geocode_candidates("大阪"))

    def test_original_comes_first(self) -> None:
        self.assertEqual(weather_tool.geocode_candidates("神戸市")[0], "神戸市")

    def test_empty_returns_nothing(self) -> None:
        self.assertEqual(weather_tool.geocode_candidates("  "), [])


class GeminiLoopTest(unittest.TestCase):
    """Tool Calling の往復が正しく組み立てられるかを、API を叩かずに確認する。"""

    def _response(self, payload: dict):
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = payload
        return response

    def test_tool_call_then_answer(self) -> None:
        first = self._response({
            "candidates": [{"content": {"parts": [
                {"functionCall": {"name": "get_weather", "args": {"date": "今日"}}}
            ]}}]
        })
        second = self._response({
            "candidates": [{"content": {"parts": [{"text": "今日は雨の予報です。"}]}}]
        })

        weather = {"ok": True, "weather": "雨", "precipitation_probability": 90}
        with mock.patch.object(gemini.httpx, "post", side_effect=[first, second]) as post, \
             mock.patch.dict(registry.HANDLERS, {"get_weather": lambda **kw: weather}):
            result = gemini.ask("今日雨降る？")

        self.assertEqual(result["text"], "今日は雨の予報です。")
        self.assertEqual(result["tools"], ["get_weather"])

        # 2回目のリクエストに functionResponse が積まれていること
        second_contents = post.call_args_list[1].kwargs["json"]["contents"]
        function_responses = [
            part for content in second_contents for part in content["parts"] if "functionResponse" in part
        ]
        self.assertEqual(len(function_responses), 1)
        self.assertEqual(function_responses[0]["functionResponse"]["response"], weather)

    def test_plain_answer_without_tools(self) -> None:
        response = self._response({
            "candidates": [{"content": {"parts": [{"text": "interface は実装を持たない型です。"}]}}]
        })
        with mock.patch.object(gemini.httpx, "post", return_value=response):
            result = gemini.ask("Javaのinterfaceって何？")
        self.assertEqual(result["tools"], [])

    def test_infinite_tool_loop_is_cut_off(self) -> None:
        looping = self._response({
            "candidates": [{"content": {"parts": [
                {"functionCall": {"name": "get_current_time", "args": {}}}
            ]}}]
        })
        with mock.patch.object(gemini.httpx, "post", return_value=looping):
            with self.assertRaises(gemini.GeminiError):
                gemini.ask("今何時？")

    def test_api_error_becomes_user_facing_message(self) -> None:
        error = mock.Mock()
        error.status_code = 403
        error.json.return_value = {"error": {"message": "API key not valid"}}
        with mock.patch.object(gemini.httpx, "post", return_value=error):
            with self.assertRaises(gemini.GeminiError) as ctx:
                gemini.ask("こんにちは")
        self.assertIn("APIキー", str(ctx.exception))

    def test_transient_overload_is_retried(self) -> None:
        """503（混雑）は一度きりで諦めず、少し待って送り直す。"""
        busy = mock.Mock()
        busy.status_code = 503
        busy.json.return_value = {"error": {"message": "This model is currently experiencing high demand."}}
        good = self._response({
            "candidates": [{"content": {"parts": [{"text": "はい。"}]}}]
        })
        with mock.patch.object(gemini.httpx, "post", side_effect=[busy, good]) as post, \
             mock.patch.object(gemini.time, "sleep") as sleep:
            result = gemini.ask("こんにちは")

        self.assertEqual(result["text"], "はい。")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once()

    def test_retries_give_up_with_a_clear_message(self) -> None:
        busy = mock.Mock()
        busy.status_code = 503
        busy.json.return_value = {"error": {"message": "high demand"}}
        with mock.patch.object(gemini.httpx, "post", return_value=busy) as post, \
             mock.patch.object(gemini.time, "sleep"):
            with self.assertRaises(gemini.GeminiError) as ctx:
                gemini.ask("こんにちは")

        self.assertIn("混み合っています", str(ctx.exception))
        self.assertEqual(post.call_count, 3)   # 初回 + 再試行2回

    def test_retrying_stops_at_the_time_budget(self) -> None:
        """混雑時は1回の応答も遅いので、回数ではなく時間で打ち切る。"""
        busy = mock.Mock()
        busy.status_code = 503
        busy.json.return_value = {"error": {"message": "high demand"}}

        clock = [0.0]
        with mock.patch.object(gemini.httpx, "post", return_value=busy) as post, \
             mock.patch.object(gemini.time, "sleep"), \
             mock.patch.object(gemini.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(gemini.config, "RETRY_BUDGET_SECONDS", 45):
            # 1回目の応答に予算いっぱいかかった状況を作る
            def slow(*args, **kwargs):
                clock[0] += 50
                return busy
            post.side_effect = slow
            with self.assertRaises(gemini.GeminiError):
                gemini.ask("こんにちは")

        self.assertEqual(post.call_count, 1)   # 予算超過で再試行しない

    def test_permanent_error_is_not_retried(self) -> None:
        denied = mock.Mock()
        denied.status_code = 403
        denied.json.return_value = {"error": {"message": "API key not valid"}}
        with mock.patch.object(gemini.httpx, "post", return_value=denied) as post:
            with self.assertRaises(gemini.GeminiError):
                gemini.ask("こんにちは")
        self.assertEqual(post.call_count, 1)

    def test_connection_failure_message(self) -> None:
        with mock.patch.object(gemini.httpx, "post", side_effect=gemini.httpx.ConnectError("x")), \
             mock.patch.object(gemini.time, "sleep"):
            with self.assertRaises(gemini.GeminiError) as ctx:
                gemini.ask("こんにちは")
        self.assertIn("接続できません", str(ctx.exception))


class MemoryTest(unittest.TestCase):
    """長期記憶。Gemini はステートレスなので、記憶は Pi 側の責任になる。"""

    def setUp(self) -> None:
        db.init_db()
        db.execute("DELETE FROM memory")

    def test_remember_and_recall(self) -> None:
        self.assertTrue(memory_tool.remember(key="情報処理の担当", value="山田先生",
                                             category="teacher")["ok"])
        found = memory_tool.recall(query="山田")
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["memories"][0]["value"], "山田先生")

    def test_same_key_overwrites(self) -> None:
        """覚え直しは増殖ではなく上書きになる。"""
        memory_tool.remember(key="情報処理の担当", value="山田先生", category="teacher")
        memory_tool.remember(key="情報処理の担当", value="佐藤先生", category="teacher")
        everything = memory_tool.recall()
        self.assertEqual(everything["count"], 1)
        self.assertEqual(everything["memories"][0]["value"], "佐藤先生")

    def test_forget(self) -> None:
        memory_tool.remember(key="部活", value="火曜と木曜")
        self.assertTrue(memory_tool.forget("部活")["ok"])
        self.assertEqual(memory_tool.recall()["count"], 0)

    def test_forget_unknown_key_is_reported(self) -> None:
        result = memory_tool.forget("知らない項目")
        self.assertFalse(result["ok"])

    def test_unparsable_expiry_is_rejected(self) -> None:
        """期限を解釈できないときに黙って今日へ丸めない（誤って消えるのを防ぐ）。"""
        result = memory_tool.remember(key="何か", value="内容", expires="いつか")
        self.assertFalse(result["ok"])
        self.assertIn("解釈できません", result["error"])
        self.assertEqual(memory_tool.recall()["count"], 0)

    def test_expired_memory_disappears(self) -> None:
        memory_tool.remember(key="数学プリント", value="水曜提出",
                             category="assignment", expires="明日")
        past = (time_tool.now() - timedelta(days=1)).isoformat(timespec="seconds")
        db.execute("UPDATE memory SET expires_at = ? WHERE key = ?", (past, "数学プリント"))

        self.assertEqual(memory_tool.recall()["count"], 0)
        self.assertEqual(memory_tool.purge_expired(), 1)

    def test_context_carries_memories(self) -> None:
        memory_tool.remember(key="情報処理の担当", value="山田先生", category="teacher")
        context = memory_tool.as_context()
        self.assertIn("情報処理の担当", context)
        self.assertIn("山田先生", context)
        self.assertIn("教員", context)

    def test_context_is_empty_without_memories(self) -> None:
        self.assertEqual(memory_tool.as_context(), "")


class TimetableTest(unittest.TestCase):
    def setUp(self) -> None:
        db.init_db()
        db.execute("DELETE FROM timetable")

    def test_weekday_forms(self) -> None:
        for value in ("木", "木曜", "木曜日", 3, "3", "thu", "thursday"):
            with self.subTest(value=value):
                self.assertEqual(timetable_tool.parse_weekday(value), 3)

    def test_unknown_weekday_is_rejected(self) -> None:
        self.assertIsNone(timetable_tool.parse_weekday("にゃー"))
        result = timetable_tool.set_timetable(weekday="にゃー", period=1, subject="情報")
        self.assertFalse(result["ok"])

    def test_period_times_come_from_settings(self) -> None:
        """1限の開始は授業開始時刻。2限以降はコマ長＋休みで積み上げる。"""
        result = timetable_tool.set_timetable(weekday="月", period=3, subject="数学")
        self.assertTrue(result["ok"])
        # 09:00 開始 / 50分 + 休み10分 なので 3限は 11:00
        self.assertEqual(result["start_time"], "11:00")
        self.assertEqual(result["end_time"], "11:50")

    def test_same_slot_overwrites(self) -> None:
        timetable_tool.set_timetable(weekday="木", period=1, subject="情報処理")
        timetable_tool.set_timetable(weekday="木", period=1, subject="英語")
        lessons = timetable_tool.get_timetable(weekday="木")["lessons"]
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0]["subject"], "英語")

    def test_missing_subject_is_rejected(self) -> None:
        self.assertFalse(timetable_tool.set_timetable(weekday="月", period=1, subject="  ")["ok"])

    def test_get_by_date_resolves_weekday(self) -> None:
        timetable_tool.set_timetable(weekday="木", period=1, subject="情報処理")
        # 2026-09-10 は木曜
        result = timetable_tool.get_timetable(date="2026-09-10")
        self.assertEqual(result["weekday_label"], "木")
        self.assertEqual(result["count"], 1)


class CalendarMergeTest(unittest.TestCase):
    """時間割を予定へ合成する。これで Google カレンダー無しでも出発時刻を逆算できる。"""

    def setUp(self) -> None:
        db.init_db()
        db.execute("DELETE FROM timetable")
        db.execute("DELETE FROM local_event")
        user_settings.save({"timetable_enabled": True, "calendar_source": "local"})

    def test_timetable_appears_in_calendar(self) -> None:
        today = time_tool.now()
        timetable_tool.set_timetable(
            weekday=today.weekday(), period=1, subject="情報処理", room="A302"
        )
        result = calendar_tool.get_calendar(days=1)

        self.assertTrue(result["ok"])
        self.assertIn("timetable", result["source"])
        titles = [event["title"] for event in result["events"]]
        self.assertIn("1限 情報処理", titles)

    def test_lessons_are_marked_as_timetable(self) -> None:
        """確定した予定ではなく時間割由来だと分かるようにしておく。"""
        today = time_tool.now()
        timetable_tool.set_timetable(weekday=today.weekday(), period=1, subject="情報処理")
        events = calendar_tool.get_calendar(days=1)["events"]
        self.assertEqual(events[0]["source"], "timetable")

    def test_events_are_sorted_together(self) -> None:
        today = time_tool.now()
        timetable_tool.set_timetable(weekday=today.weekday(), period=1, subject="情報処理")
        calendar_tool.create_event(title="朝の用事", start=today.strftime("%Y-%m-%dT07:30"))

        events = calendar_tool.get_calendar(days=1)["events"]
        starts = [event["start"] for event in events]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(events[0]["title"], "朝の用事")

    def test_google_events_are_merged_and_ordered(self) -> None:
        """Google 接続時も時間割と混ぜて時刻順に並ぶ。"""
        today = time_tool.now()
        timetable_tool.set_timetable(weekday=today.weekday(), period=1, subject="情報処理")
        date_str = today.strftime("%Y-%m-%d")
        google = [{"id": "g1", "title": "朝の用事",
                   "start": f"{date_str}T07:30:00+09:00", "end": "", "location": "",
                   "all_day": False, "source": "google"}]

        user_settings.save({"calendar_source": "google"})
        try:
            with mock.patch.object(calendar_tool, "_google_events", return_value=google):
                result = calendar_tool.get_calendar(date=date_str, days=1)
        finally:
            user_settings.save({"calendar_source": "local"})

        self.assertEqual(result["source"], "google+timetable")
        self.assertEqual([event["title"] for event in result["events"]],
                         ["朝の用事", "1限 情報処理"])

    def test_other_timezone_events_are_placed_correctly(self) -> None:
        """カレンダーのタイムゾーンが JST 以外でも並び順が狂わない。

        文字列比較のままだと 00:30Z（= JST 09:30）が 09:00 の授業より前に来る。
        """
        today = time_tool.now()
        timetable_tool.set_timetable(weekday=today.weekday(), period=1, subject="情報処理")
        date_str = today.strftime("%Y-%m-%d")
        google = [{"id": "g1", "title": "UTCで返る予定",
                   "start": f"{date_str}T00:30:00+00:00", "end": "", "location": "",
                   "all_day": False, "source": "google"}]

        user_settings.save({"calendar_source": "google"})
        try:
            with mock.patch.object(calendar_tool, "_google_events", return_value=google):
                result = calendar_tool.get_calendar(date=date_str, days=1)
        finally:
            user_settings.save({"calendar_source": "local"})

        # JST 09:30 なので、09:00 の授業より後ろに来る
        self.assertEqual([event["title"] for event in result["events"]],
                         ["1限 情報処理", "UTCで返る予定"])

    def test_all_day_events_come_first(self) -> None:
        today = time_tool.now()
        timetable_tool.set_timetable(weekday=today.weekday(), period=1, subject="情報処理")
        date_str = today.strftime("%Y-%m-%d")
        google = [{"id": "g1", "title": "文化祭", "start": date_str, "end": "",
                   "location": "", "all_day": True, "source": "google"}]

        user_settings.save({"calendar_source": "google"})
        try:
            with mock.patch.object(calendar_tool, "_google_events", return_value=google):
                result = calendar_tool.get_calendar(date=date_str, days=1)
        finally:
            user_settings.save({"calendar_source": "local"})

        self.assertEqual(result["events"][0]["title"], "文化祭")

    def test_google_failure_falls_back_and_says_so(self) -> None:
        """Google に繋がらないときは黙って諦めず、ローカルを見たことを示す。"""
        user_settings.save({"calendar_source": "google"})
        try:
            with mock.patch.object(calendar_tool, "_google_events", return_value=None):
                result = calendar_tool.get_calendar(days=1)
        finally:
            user_settings.save({"calendar_source": "local"})
        self.assertTrue(result["ok"])
        self.assertIn("local(fallback)", result["source"])

    def test_can_be_switched_off(self) -> None:
        today = time_tool.now()
        timetable_tool.set_timetable(weekday=today.weekday(), period=1, subject="情報処理")
        user_settings.save({"timetable_enabled": False})
        try:
            result = calendar_tool.get_calendar(days=1)
        finally:
            user_settings.save({"timetable_enabled": True})
        self.assertEqual(result["count"], 0)
        self.assertNotIn("timetable", result["source"])


class CreateEventTest(unittest.TestCase):
    def setUp(self) -> None:
        db.init_db()
        db.execute("DELETE FROM local_event")

    def test_natural_time_is_accepted(self) -> None:
        result = calendar_tool.create_event(title="歯医者", start="明日 15:00")
        self.assertTrue(result["ok"])
        expected = (time_tool.now() + timedelta(days=1)).strftime("%Y-%m-%d 15:00")
        self.assertEqual(result["start"], expected)

    def test_unparsable_time_does_not_create_anything(self) -> None:
        """仕様書 22章: 解釈できないときに勝手な日時で登録しない。"""
        result = calendar_tool.create_event(title="歯医者", start="そのうち")
        self.assertFalse(result["ok"])
        self.assertEqual(db.query("SELECT * FROM local_event"), [])

    def test_title_is_required(self) -> None:
        self.assertFalse(calendar_tool.create_event(title="", start="明日 15:00")["ok"])

    def test_gemini_style_datetime_argument(self) -> None:
        """Gemini が start ではなく datetime で渡してきても受け取れる。"""
        result = calendar_tool.create_event(title="検定", datetime="2026-10-15T09:00")
        self.assertTrue(result["ok"])
        self.assertEqual(result["start"], "2026-10-15 09:00")


class CalendarSourceGuardTest(unittest.TestCase):
    """未連携のまま Google を取得元にすると、黙ってローカルで動いて誤解を生む。"""

    def setUp(self) -> None:
        user_settings.save({"calendar_source": "local"})

    def tearDown(self) -> None:
        user_settings.save({"calendar_source": "local"})

    def _apply(self, values: dict, connected: bool) -> dict:
        from backend.api import setup as setup_api

        with mock.patch.object(setup_api.calendar_tool, "google_connected", return_value=connected):
            return setup_api.apply_profile(values)

    def test_unconnected_google_is_not_selected(self) -> None:
        result = self._apply({"calendar_source": "google"}, connected=False)
        self.assertEqual(result["settings"]["calendar_source"], "local")
        self.assertIn("未連携", result["warning"])

    def test_connected_google_is_accepted(self) -> None:
        result = self._apply({"calendar_source": "google"}, connected=True)
        self.assertEqual(result["settings"]["calendar_source"], "google")
        self.assertEqual(result["warning"], "")

    def test_other_values_are_still_saved(self) -> None:
        """取得元だけ弾いて、同時に送られた他の項目は保存する。"""
        result = self._apply(
            {"calendar_source": "google", "user_name": "たろう", "buffer_minutes": 15},
            connected=False,
        )
        self.assertEqual(result["settings"]["user_name"], "たろう")
        self.assertEqual(result["settings"]["buffer_minutes"], 15)
        self.assertEqual(result["settings"]["calendar_source"], "local")

    def test_switching_back_to_local_always_works(self) -> None:
        result = self._apply({"calendar_source": "local"}, connected=False)
        self.assertEqual(result["settings"]["calendar_source"], "local")
        self.assertEqual(result["warning"], "")


class GoogleOAuthTest(unittest.TestCase):
    """画面だけで OAuth を完了させる経路。Google へは接続せずに検証する。"""

    def setUp(self) -> None:
        from backend.api import google as google_api

        self.api = google_api
        self.secret = Path(config.GOOGLE_OAUTH_CLIENT_SECRET_FILE)
        self.token = Path(config.GOOGLE_OAUTH_TOKEN_FILE)
        self.secret.parent.mkdir(parents=True, exist_ok=True)
        self.secret.unlink(missing_ok=True)
        self.token.unlink(missing_ok=True)
        google_api._pending.clear()
        user_settings.save({"calendar_source": "local"})

    def tearDown(self) -> None:
        self.secret.unlink(missing_ok=True)
        self.token.unlink(missing_ok=True)
        self.api._pending.clear()
        user_settings.save({"calendar_source": "local"})

    # --- 貼り付けられた内容の解釈 ---

    def test_extracts_code_from_pasted_url(self) -> None:
        code, state, error = self.api.extract_code(
            "http://127.0.0.1:8765/?state=abc&code=4/0Axyz&scope=https://www.googleapis.com/auth/calendar.readonly"
        )
        self.assertEqual(code, "4/0Axyz")
        self.assertEqual(state, "abc")
        self.assertEqual(error, "")

    def test_accepts_a_bare_code(self) -> None:
        code, state, _ = self.api.extract_code("4/0Axyz")
        self.assertEqual(code, "4/0Axyz")
        self.assertEqual(state, "")

    def test_detects_denied_consent(self) -> None:
        _code, _state, error = self.api.extract_code(
            "http://127.0.0.1:8765/?error=access_denied&state=abc"
        )
        self.assertEqual(error, "access_denied")

    def test_denied_consent_is_reported(self) -> None:
        result = self.api.finish(self.api.Redirected(
            redirected_url="http://127.0.0.1:8765/?error=access_denied&state=abc"))
        self.assertFalse(result["ok"])
        self.assertIn("許可されませんでした", result["error"])

    # --- クライアント JSON の受け取り ---

    def _upload(self, payload: bytes, filename: str = "client_secret.json") -> dict:
        import asyncio

        upload = mock.Mock()
        upload.read = mock.AsyncMock(return_value=payload)
        upload.filename = filename
        return asyncio.run(self.api.upload_client_secret(upload))

    def test_desktop_client_is_saved(self) -> None:
        payload = json.dumps({"installed": {"client_id": "x.apps.googleusercontent.com",
                                            "client_secret": "s",
                                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                                            "token_uri": "https://oauth2.googleapis.com/token"}})
        result = self._upload(payload.encode("utf-8"))
        self.assertTrue(result["ok"])
        self.assertTrue(self.secret.exists())
        self.assertTrue(result["client_secret_saved"])

    def test_web_client_is_rejected_with_the_reason(self) -> None:
        """ウェブアプリ型はループバックへ返せないので、ここで気づけるようにする。"""
        payload = json.dumps({"web": {"client_id": "x", "client_secret": "s"}})
        result = self._upload(payload.encode("utf-8"))
        self.assertFalse(result["ok"])
        self.assertIn("デスクトップアプリ", result["error"])

    def test_broken_json_is_rejected(self) -> None:
        result = self._upload(b"{ not json")
        self.assertFalse(result["ok"])
        self.assertIn("JSON", result["error"])

    def test_empty_file_is_rejected(self) -> None:
        self.assertFalse(self._upload(b"")["ok"])

    # --- 認証の開始と完了 ---

    def test_start_requires_the_client_secret(self) -> None:
        result = self.api.start()
        self.assertFalse(result["ok"])
        self.assertIn("JSON", result["error"])

    def _fake_flow(self, refresh_token: str = "refresh-me"):
        creds = mock.Mock()
        creds.refresh_token = refresh_token
        creds.to_json.return_value = json.dumps({"token": "t", "refresh_token": refresh_token})
        flow = mock.Mock()
        flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth?x=1", "state-1")
        flow.credentials = creds
        return flow

    def test_start_then_finish_saves_the_token(self) -> None:
        self.secret.write_text("{}", encoding="utf-8")
        flow = self._fake_flow()

        with mock.patch("google_auth_oauthlib.flow.Flow.from_client_secrets_file", return_value=flow):
            started = self.api.start()
        self.assertTrue(started["ok"])
        self.assertEqual(started["state"], "state-1")
        # 更新用トークンが返るよう offline+consent で要求している
        kwargs = flow.authorization_url.call_args.kwargs
        self.assertEqual(kwargs["access_type"], "offline")
        self.assertEqual(kwargs["prompt"], "consent")

        result = self.api.finish(self.api.Redirected(
            redirected_url="http://127.0.0.1:8765/?state=state-1&code=4/0Axyz"))

        self.assertTrue(result["ok"])
        flow.fetch_token.assert_called_once_with(code="4/0Axyz")
        self.assertTrue(self.token.exists())
        self.assertIn("refresh_token", self.token.read_text(encoding="utf-8"))
        # 連携できたら取得元も切り替える
        self.assertEqual(user_settings.get("calendar_source"), "google")

    def test_missing_refresh_token_is_refused(self) -> None:
        """更新用トークンが無いと1時間で切れる。連携済みに見せない。"""
        self.secret.write_text("{}", encoding="utf-8")
        flow = self._fake_flow(refresh_token="")
        with mock.patch("google_auth_oauthlib.flow.Flow.from_client_secrets_file", return_value=flow):
            self.api.start()
        result = self.api.finish(self.api.Redirected(
            redirected_url="http://127.0.0.1:8765/?state=state-1&code=4/0Axyz"))

        self.assertFalse(result["ok"])
        self.assertIn("更新用", result["error"])
        self.assertFalse(self.token.exists())
        self.assertEqual(user_settings.get("calendar_source"), "local")

    def test_finish_without_start_is_reported(self) -> None:
        result = self.api.finish(self.api.Redirected(
            redirected_url="http://127.0.0.1:8765/?state=unknown&code=4/0Axyz"))
        self.assertFalse(result["ok"])
        self.assertIn("やり直して", result["error"])

    def test_url_without_a_code_is_reported(self) -> None:
        result = self.api.finish(self.api.Redirected(redirected_url="http://127.0.0.1:8765/?foo=1"))
        self.assertFalse(result["ok"])
        self.assertIn("認証コード", result["error"])

    def test_token_exchange_failure_keeps_things_unlinked(self) -> None:
        self.secret.write_text("{}", encoding="utf-8")
        flow = self._fake_flow()
        flow.fetch_token.side_effect = ValueError("bad code")
        with mock.patch("google_auth_oauthlib.flow.Flow.from_client_secrets_file", return_value=flow):
            self.api.start()
        result = self.api.finish(self.api.Redirected(
            redirected_url="http://127.0.0.1:8765/?state=state-1&code=wrong"))

        self.assertFalse(result["ok"])
        self.assertFalse(self.token.exists())
        self.assertEqual(user_settings.get("calendar_source"), "local")

    def test_disconnect_removes_the_token(self) -> None:
        self.token.write_text("{}", encoding="utf-8")
        user_settings.save({"calendar_source": "google"})
        result = self.api.disconnect()
        self.assertTrue(result["ok"])
        self.assertFalse(self.token.exists())
        self.assertEqual(user_settings.get("calendar_source"), "local")

    def test_redirect_uri_is_loopback(self) -> None:
        """デスクトップアプリ型が許すのはループバックのみ。"""
        self.assertTrue(self.api.REDIRECT_URI.startswith("http://127.0.0.1"))


class ChatMemoryWiringTest(unittest.TestCase):
    """記憶がシステムプロンプトへ実際に差し込まれるかを確認する。"""

    def setUp(self) -> None:
        db.init_db()
        db.execute("DELETE FROM memory")

    def test_memories_reach_gemini_as_context(self) -> None:
        from backend.api import chat as chat_api

        memory_tool.remember(key="情報処理の担当", value="山田先生", category="teacher")
        answer = {"text": "山田先生です。", "tools": [], "tool_results": []}

        with mock.patch.object(chat_api.gemini, "ask", return_value=answer) as ask:
            chat_api.chat(chat_api.ChatRequest(message="情報の先生だれ？"))

        context = ask.call_args.kwargs["context"]
        self.assertIn("情報処理の担当", context)
        self.assertIn("山田先生", context)


if __name__ == "__main__":
    unittest.main()
