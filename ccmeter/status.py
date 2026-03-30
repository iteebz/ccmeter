"""Show current collection status."""

import os

from ccmeter.db import DB_PATH, connect
from ccmeter.display import BOLD, CYAN, DIM, GREEN, RED, WHITE, YELLOW, ago, c, hr


def _daemon_status() -> tuple[str, str]:
    """Check if the poll daemon is alive. Returns (status_text, color)."""
    pidfile = DB_PATH.parent / "poll.pid"
    if not pidfile.exists():
        return "not running", DIM
    try:
        pid = int(pidfile.read_text().strip())
        os.kill(pid, 0)
        return f"running (pid {pid})", GREEN
    except (ValueError, OSError):
        return "stale pidfile", YELLOW


def _db_size() -> str:
    """Human-readable DB file size."""
    if not DB_PATH.exists():
        return "0"
    size = DB_PATH.stat().st_size
    if size >= 1_000_000:
        return f"{size / 1_000_000:.0f}MB"
    if size >= 1_000:
        return f"{size / 1_000:.0f}KB"
    return f"{size}B"


def show_status():
    if not DB_PATH.exists():
        print("no data collected yet. run: ccmeter poll")
        return

    conn = connect()

    total = conn.execute("SELECT COUNT(*) as n FROM usage_samples").fetchone()["n"]
    latest = conn.execute("SELECT ts FROM usage_samples ORDER BY ts DESC LIMIT 1").fetchone()
    oldest = conn.execute("SELECT ts FROM usage_samples ORDER BY ts ASC LIMIT 1").fetchone()

    # per-bucket current state
    current = conn.execute(
        """SELECT bucket, utilization, ts FROM usage_samples
           WHERE id IN (SELECT MAX(id) FROM usage_samples GROUP BY bucket)
           ORDER BY bucket"""
    ).fetchall()

    # collection gaps: samples in last 24h
    recent_count = conn.execute(
        "SELECT COUNT(*) as n FROM usage_samples WHERE ts > datetime('now', '-24 hours')"
    ).fetchone()["n"]

    conn.close()

    daemon_text, daemon_color = _daemon_status()

    print()
    print(f"  {c(BOLD + WHITE, 'ccmeter status')}")
    print(f"  {hr()}")
    print(f"  {c(DIM, 'daemon')}   {c(daemon_color, daemon_text)}")
    print(f"  {c(DIM, 'db')}       {c(DIM, _db_size())}  {c(DIM, str(DB_PATH))}")
    print(f"  {c(DIM, 'samples')}  {c(WHITE, total)}  {c(DIM, f'({recent_count} last 24h)')}")
    if latest:
        freshness = ago(latest["ts"])
        fresh_color = GREEN if "just now" in freshness or "m ago" in freshness else YELLOW if "h ago" in freshness else RED
        print(f"  {c(DIM, 'latest')}   {c(fresh_color, freshness)}")
    if oldest and latest:
        print(f"  {c(DIM, 'range')}    {c(DIM, oldest['ts'][:16])} → {c(DIM, latest['ts'][:16])}")
    print()

    if current:
        for r in current:
            util = r["utilization"]
            color = GREEN if util < 50 else YELLOW if util < 80 else CYAN
            print(f"    {r['bucket']:<22} {c(color, f'{util:5.1f}%')}  {c(DIM, ago(r['ts']))}")
        print()
