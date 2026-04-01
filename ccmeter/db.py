"""Local sqlite storage."""

import sqlite3
from pathlib import Path

from ccmeter.migrations import migrate

DB_PATH = Path.home() / ".ccmeter" / "meter.db"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    migrate(conn)
    return conn
