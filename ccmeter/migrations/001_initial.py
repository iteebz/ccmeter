"""Initial schema."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS usage_samples (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            bucket      TEXT NOT NULL,
            utilization REAL NOT NULL,
            resets_at   TEXT,
            tier        TEXT,
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
    """)
