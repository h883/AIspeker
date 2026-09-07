"""標準ライブラリだけで動くテスト。

  python -m unittest discover -s tests

Gemini や外部 API は呼ばず、ツール解釈と Tool Calling ループの組み立てを検証する。
"""
from __future__ import annotations

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
from backend.tools import registry  # noqa: E402
from backend.tools import reminder as reminder_tool  # noqa: E402
from backend.tools import routes as routes_tool  # noqa: E402
from backend.tools import time as time_tool  # noqa: E402
from backend.tools import weather as weather_tool  # noqa: E402


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

    def test_connection_failure_message(self) -> None:
        with mock.patch.object(gemini.httpx, "post", side_effect=gemini.httpx.ConnectError("x")):
            with self.assertRaises(gemini.GeminiError) as ctx:
                gemini.ask("こんにちは")
        self.assertIn("接続できません", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
