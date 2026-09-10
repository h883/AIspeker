"""SQLite のスキーマ定義（仕様書 18章）。"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT NOT NULL,
    user_message      TEXT NOT NULL,
    assistant_message TEXT NOT NULL,
    tool_used         TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_conversation_ts ON conversation(timestamp);

CREATE TABLE IF NOT EXISTS reminder (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    datetime    TEXT NOT NULL,          -- ISO8601 (ローカルタイムゾーン)
    message     TEXT NOT NULL DEFAULT '',
    notified    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reminder_dt ON reminder(datetime);

-- Google Calendar を未接続でも予定機能が使えるようにするローカル予定表
CREATE TABLE IF NOT EXISTS local_event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    start       TEXT NOT NULL,          -- ISO8601
    end         TEXT NOT NULL DEFAULT '',
    location    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_local_event_start ON local_event(start);
"""

# 記憶と時間割（Google カレンダーに依存せず、端末内だけで予定機能を完結させるため）
SCHEMA += """
-- AI が覚えた事実。key を一意にして「覚え直し」が上書きになるようにする。
CREATE TABLE IF NOT EXISTS memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL DEFAULT 'general',
    key         TEXT NOT NULL UNIQUE,
    value       TEXT NOT NULL,
    expires_at  TEXT NOT NULL DEFAULT '',   -- ISO8601。空なら無期限
    source      TEXT NOT NULL DEFAULT 'ai', -- ai / manual（聞き間違いと手入力を区別する）
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_category ON memory(category);
CREATE INDEX IF NOT EXISTS idx_memory_expires ON memory(expires_at);

-- 毎週繰り返す時間割。曜日と時限で一意。
-- 単発の予定（local_event）とは分ける。「明日の1限は？」に確実に答えるため。
CREATE TABLE IF NOT EXISTS timetable (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    weekday     INTEGER NOT NULL,           -- 0=月 ... 6=日（datetime.weekday() と同じ）
    period      INTEGER NOT NULL,           -- 1限, 2限, ...
    subject     TEXT NOT NULL,
    teacher     TEXT NOT NULL DEFAULT '',
    room        TEXT NOT NULL DEFAULT '',
    start_time  TEXT NOT NULL DEFAULT '',   -- "09:00"。空なら授業開始時刻から計算する
    end_time    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(weekday, period)
);

CREATE INDEX IF NOT EXISTS idx_timetable_weekday ON timetable(weekday);
"""
