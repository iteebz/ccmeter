"""Cache parsed scan results per file."""

import sqlite3

# Bump this when parse logic changes to auto-invalidate.
CACHE_VERSION = 1


def up(conn: sqlite3.Connection) -> None:
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS scan_cache (
            path        TEXT PRIMARY KEY,
            mtime       REAL NOT NULL,
            size        INTEGER NOT NULL,
            version     INTEGER NOT NULL DEFAULT {CACHE_VERSION},
            events      JSON NOT NULL,
            activity    JSON NOT NULL
        );
    """)
