"""アプリ全体の設定。値は .env / 環境変数から読み込む。

APIキー類はここ（Raspberry Pi 側）にのみ置く。仕様書 21章。

セットアップ画面からキーを保存できるよう、.env の書き換えと再読み込みに対応する。
他のモジュールは config.GEMINI_API_KEY のように「呼び出し時に属性を引く」形で
参照しているため、reload() で書き換えれば再起動なしで反映される。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
FRONTEND_DIR = BASE_DIR / "frontend"
ENV_PATH = Path(os.environ.get("ENV_PATH") or (BASE_DIR / ".env"))

_write_lock = threading.Lock()

# .env で管理する項目。セットアップ画面から書き換えてよいものだけを並べる。
WRITABLE_KEYS = (
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "GOOGLE_MAPS_API_KEY",
    "GOOGLE_CALENDAR_ID",
    "TZ_NAME",
    "SAVE_AUDIO",
    "SAVE_HISTORY",
    "TTS_PROVIDER",
)

# 値を伏せて扱うべき項目（画面へは中身を返さず、設定済みかどうかだけ伝える）
SECRET_KEYS = ("GEMINI_API_KEY", "GOOGLE_MAPS_API_KEY", "OPENWEATHER_API_KEY")


def _load_dotenv(path: Path, override: bool = False) -> None:
    """依存を増やさないための最小限の .env ローダー。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override:
            os.environ[key] = value
        else:
            # 起動時は、実際の環境変数を .env で上書きしない
            os.environ.setdefault(key, value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name, "1" if default else "0").lower()
    return value in {"1", "true", "yes", "on"}


def _apply() -> None:
    """環境変数の現在値をモジュール変数へ反映する。"""
    globals().update(
        # --- サーバー ---
        HOST=_env("HOST", "0.0.0.0"),
        PORT=_env_int("PORT", 8000),
        RELOAD=_env_bool("RELOAD", False),

        # HTTPS。スマートフォンのマイク（Web Speech API）と PWA は
        # セキュアコンテキストでしか動かないため、LAN 越しに使うなら設定する。
        # python scripts/make_cert.py で自己署名証明書を作れる。
        SSL_CERT_FILE=_env("SSL_CERT_FILE"),
        SSL_KEY_FILE=_env("SSL_KEY_FILE"),

        # --- Gemini ---
        GEMINI_API_KEY=_env("GEMINI_API_KEY"),
        GEMINI_MODEL=_env("GEMINI_MODEL", "gemini-2.5-flash"),
        GEMINI_ENDPOINT=_env(
            "GEMINI_ENDPOINT", "https://generativelanguage.googleapis.com/v1beta"
        ),
        GEMINI_TIMEOUT=_env_int("GEMINI_TIMEOUT", 60),
        # Tool Calling を何往復まで許すか（無限ループ防止）
        MAX_TOOL_ROUNDS=_env_int("MAX_TOOL_ROUNDS", 6),

        # --- 外部API（未設定なら該当ツールは「取得できません」を返す。仕様書 23章）---
        GOOGLE_MAPS_API_KEY=_env("GOOGLE_MAPS_API_KEY"),
        GOOGLE_CALENDAR_ID=_env("GOOGLE_CALENDAR_ID", "primary"),
        GOOGLE_OAUTH_CLIENT_SECRET_FILE=_env(
            "GOOGLE_OAUTH_CLIENT_SECRET_FILE", str(CONFIG_DIR / "google_client_secret.json")
        ),
        GOOGLE_OAUTH_TOKEN_FILE=_env(
            "GOOGLE_OAUTH_TOKEN_FILE", str(CONFIG_DIR / "google_token.json")
        ),

        # 天気は Open-Meteo（APIキー不要）を既定にしているため、鍵なしでも動く
        WEATHER_PROVIDER=_env("WEATHER_PROVIDER", "open-meteo"),
        OPENWEATHER_API_KEY=_env("OPENWEATHER_API_KEY"),

        # --- ローカル ---
        DB_PATH=Path(_env("DB_PATH", str(DATA_DIR / "assistant.db"))),
        SETTINGS_PATH=Path(_env("SETTINGS_PATH", str(CONFIG_DIR / "settings.json"))),
        TIMEZONE=_env("TZ_NAME", "Asia/Tokyo"),

        # --- 音声（任意。既定はスマートフォンの Web Speech API を使う）---
        WHISPER_MODEL=_env("WHISPER_MODEL", "small"),
        TTS_PROVIDER=_env("TTS_PROVIDER", "browser"),  # browser / gtts

        # --- プライバシー（仕様書 24章）---
        SAVE_AUDIO=_env_bool("SAVE_AUDIO", False),
        SAVE_HISTORY=_env_bool("SAVE_HISTORY", True),
    )


def reload() -> None:
    """.env を読み直して設定を更新する。サーバーの再起動は不要。"""
    _load_dotenv(ENV_PATH, override=True)
    _apply()


def update_env(values: dict[str, str]) -> list[str]:
    """.env の指定キーを書き換えて即座に反映する。書き換えたキー名を返す。

    コメントや他の行は残したまま、該当行だけを差し替える。
    空文字を渡したキーは「変更しない」扱いにする（画面から鍵を消さないため）。
    """
    updates = {
        key: str(value).strip()
        for key, value in values.items()
        if key in WRITABLE_KEYS and str(value).strip() != ""
    }
    if not updates:
        return []

    with _write_lock:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
        remaining = dict(updates)

        for index, raw in enumerate(lines):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.partition("=")[0].strip()
            if key in remaining:
                lines[index] = f"{key}={remaining.pop(key)}"

        if remaining:
            lines.append("")
            lines.append("# セットアップ画面から追加")
            for key, value in remaining.items():
                lines.append(f"{key}={value}")

        ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reload()
    return sorted(updates)


_load_dotenv(ENV_PATH)
_apply()

DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
