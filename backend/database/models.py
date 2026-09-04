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
