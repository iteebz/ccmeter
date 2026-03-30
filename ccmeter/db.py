"""Local sqlite storage for usage samples."""

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".cc-meter" / "meter.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    bucket      TEXT NOT NULL,
    utilization REAL NOT NULL,
    resets_at   TEXT,
    raw         JSON
);

CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_samples(ts);
CREATE INDEX IF NOT EXISTS idx_usage_bucket_ts ON usage_samples(bucket, ts);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL UNIQUE,
    project     TEXT,
    started_at  TEXT,
    last_seen   TEXT,
    total_input_tokens  INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_id ON sessions(session_id);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
