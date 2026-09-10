"""AI の長期記憶。

Gemini API はステートレスで、毎回こちらから文脈を送り直している。
つまり「記憶」は AI 側の機能ではなく、保存・検索・プロンプトへの差し込みという
ただのデータ処理であり、すべて Raspberry Pi 内で完結できる。

覚えた内容は SQLite にのみ置き、gemini.ask(context=...) から
システムプロンプトへ差し込む。key は一意なので「覚え直し」は上書きになる。
"""
from __future__ import annotations

from datetime import datetime, time as time_of_day, timedelta
from typing import Any

from backend.database import db
from backend.tools.time import now, tz

# 学校生活で使う分類。未知の値が来ても general として受け入れる。
CATEGORIES = {
    "teacher": "教員",
    "assignment": "課題",
    "club": "部活",
    "exam": "試験",
    "school": "学校",
    "preference": "好み",
    "general": "その他",
}

# context へ流し込む上限。無制限に増やすと入力トークンを圧迫するため。
CONTEXT_LIMIT = 80
CONTEXT_MAX_CHARS = 4000


def _label(category: str) -> str:
    return CATEGORIES.get(category, CATEGORIES["general"])


def _normalize_category(category: str | None) -> str:
    value = (category or "").strip().lower()
    return value if value in CATEGORIES else "general"


def parse_expiry(value: str | None) -> tuple[str, bool]:
    """期限文字列を ISO8601 に正規化する。

    (正規化した値, 解釈できたか) を返す。空入力は「無期限」として成功扱い。
    解釈できなかったときに黙って今日へ丸めないのは、
    誤った期限で記憶が消えるのを防ぐため（仕様書 22章の考え方）。
    """
    text = (value or "").strip()
    if not text:
        return "", True

    lowered = text.lower()
    offsets = {
        "今日": 0, "きょう": 0, "today": 0,
        "明日": 1, "あした": 1, "あす": 1, "tomorrow": 1,
        "明後日": 2, "あさって": 2,
        "来週": 7, "らいしゅう": 7, "next week": 7,
        "来月": 30, "らいげつ": 30,
    }
    if lowered in offsets:
        day = (now() + timedelta(days=offsets[lowered])).date()
        return datetime.combine(day, time_of_day(23, 59), tzinfo=tz()).isoformat(timespec="seconds"), True

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return "", False
    if not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=tz())
    # 日付だけ渡された場合はその日の終わりまで有効とみなす
    if parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
        parsed = parsed.replace(hour=23, minute=59)
    return parsed.isoformat(timespec="seconds"), True


def purge_expired() -> int:
    """期限切れの記憶を消す。件数を返す。"""
    current = now().isoformat(timespec="seconds")
    rows = db.query(
        "SELECT id FROM memory WHERE expires_at != '' AND expires_at <= ?", (current,)
    )
    for row in rows:
        db.execute("DELETE FROM memory WHERE id = ?", (row["id"],))
    return len(rows)


def _rows(category: str = "", include_expired: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM memory"
    params: list[Any] = []
    clauses = []
    if category:
        clauses.append("category = ?")
        params.append(category)
    if not include_expired:
        clauses.append("(expires_at = '' OR expires_at > ?)")
        params.append(now().isoformat(timespec="seconds"))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY category, updated_at DESC"
    return db.query(sql, tuple(params))


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "category": row["category"],
        "category_label": _label(row["category"]),
        "key": row["key"],
        "value": row["value"],
        "expires_at": row["expires_at"][:16].replace("T", " ") if row["expires_at"] else "",
        "source": row["source"],
        "updated_at": row["updated_at"][:16].replace("T", " "),
    }


# --- Tool Calling から呼ばれる ---

def remember(
    key: str,
    value: str,
    category: str | None = None,
    expires: str | None = None,
    source: str = "ai",
) -> dict[str, Any]:
    """事実をひとつ覚える。同じ key なら上書きする。"""
    key = (key or "").strip()
    value = (value or "").strip()
    if not key:
        return {"ok": False, "error": "覚える項目の名前が指定されていません。"}
    if not value:
        return {"ok": False, "error": "覚える内容が指定されていません。"}

    expires_at, parsed = parse_expiry(expires)
    if not parsed:
        return {"ok": False, "error": f"期限を解釈できませんでした（{expires}）。"}

    stamp = now().isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO memory (category, key, value, expires_at, source, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET"
        "   category = excluded.category,"
        "   value = excluded.value,"
        "   expires_at = excluded.expires_at,"
        "   source = excluded.source,"
        "   updated_at = excluded.updated_at",
        (_normalize_category(category), key, value, expires_at, source, stamp, stamp),
    )
    return {
        "ok": True,
        "key": key,
        "value": value,
        "category": _normalize_category(category),
        "expires_at": expires_at[:16].replace("T", " ") if expires_at else "",
    }


def recall(query: str | None = None, category: str | None = None) -> dict[str, Any]:
    """覚えていることを探す。query 省略時はすべて返す。"""
    wanted = (query or "").strip().lower()
    filter_category = (category or "").strip().lower()
    rows = _rows(category=filter_category if filter_category in CATEGORIES else "")

    if wanted:
        rows = [
            row for row in rows
            if wanted in row["key"].lower() or wanted in row["value"].lower()
        ]

    return {
        "ok": True,
        "count": len(rows),
        "memories": [
            {"key": row["key"], "value": row["value"],
             "category": row["category"],
             "expires_at": row["expires_at"][:16].replace("T", " ") if row["expires_at"] else ""}
            for row in rows
        ],
    }


def forget(key: str) -> dict[str, Any]:
    """覚えていることを消す。"""
    key = (key or "").strip()
    if not key:
        return {"ok": False, "error": "消す項目の名前が指定されていません。"}
    existing = db.query("SELECT id FROM memory WHERE key = ?", (key,))
    if not existing:
        return {"ok": False, "error": f"「{key}」は覚えていません。"}
    db.execute("DELETE FROM memory WHERE key = ?", (key,))
    return {"ok": True, "key": key}


# --- 画面（REST）から呼ばれる ---

def list_memories(include_expired: bool = False) -> dict[str, Any]:
    rows = _rows(include_expired=include_expired)
    return {
        "ok": True,
        "count": len(rows),
        "categories": CATEGORIES,
        "memories": [_public(row) for row in rows],
    }


def delete_memory(memory_id: int) -> dict[str, Any]:
    db.execute("DELETE FROM memory WHERE id = ?", (memory_id,))
    return {"ok": True}


def clear_memories() -> dict[str, Any]:
    db.execute("DELETE FROM memory")
    return {"ok": True}


# --- システムプロンプトへの差し込み ---

def as_context() -> str:
    """覚えていることを systemInstruction へ載せる形にする。

    件数が少ないうちは検索せず全部渡すのが確実で速い。
    増えてきたら CONTEXT_LIMIT で頭打ちにする。
    """
    purge_expired()
    rows = _rows()
    if not rows:
        return ""

    lines = ["以下はユーザーについて覚えている事実である。質問に答えるとき参考にしてよい。"]
    for row in rows[:CONTEXT_LIMIT]:
        suffix = f"（期限: {row['expires_at'][:10]}）" if row["expires_at"] else ""
        lines.append(f"- [{_label(row['category'])}] {row['key']}: {row['value']}{suffix}")
        if sum(len(line) for line in lines) > CONTEXT_MAX_CHARS:
            break
    return "\n".join(lines)
