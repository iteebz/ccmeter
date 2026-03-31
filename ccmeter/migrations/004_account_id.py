"""Add account_id to usage_samples for multi-account support."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        ALTER TABLE usage_samples ADD COLUMN account_id TEXT;
        CREATE INDEX IF NOT EXISTS idx_usage_account ON usage_samples(account_id);
        CREATE INDEX IF NOT EXISTS idx_usage_account_bucket ON usage_samples(account_id, bucket, ts);
    """)
