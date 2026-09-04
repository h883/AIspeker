"""SQLite への薄いアクセス層。追加依存なしで扱えるよう標準 sqlite3 を使う。"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from backend import config
from backend.database.models import SCHEMA

_lock = threading.Lock()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)


def query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with _lock, connect() as conn:
        cur = conn.execute(sql, params)
        return int(cur.lastrowid or 0)


# --- 会話履歴 ---

def save_conversation(user_message: str, assistant_message: str, tools: list[str]) -> None:
    if not config.SAVE_HISTORY:
        return
    execute(
        "INSERT INTO conversation (timestamp, user_message, assistant_message, tool_used)"
        " VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), user_message, assistant_message, ",".join(tools)),
    )


def recent_conversations(limit: int = 50) -> list[dict[str, Any]]:
    return query(
        "SELECT * FROM conversation ORDER BY id DESC LIMIT ?", (limit,)
    )


def clear_conversations() -> None:
    execute("DELETE FROM conversation")
