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


if __name__ == "__main__":
    unittest.main()
