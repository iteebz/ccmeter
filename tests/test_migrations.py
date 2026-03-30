"""Test migration runner."""

import sqlite3
from pathlib import Path

from ccmeter.migrations import migrate


def test_migrate_creates_tables(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    migrate(conn)

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "usage_samples" in tables
    assert "sessions" in tables
    assert "_migrations" in tables

    applied = {r[0] for r in conn.execute("SELECT name FROM _migrations").fetchall()}
    assert "001_initial" in applied
    conn.close()


def test_migrate_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    migrate(conn)
    migrate(conn)

    applied = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    assert applied == 2
    conn.close()
