"""Show current collection status."""

import json
import os
from typing import Any

from ccmeter.auth import fetch_account_id, get_credentials
from ccmeter.db import DB_PATH, connect
from ccmeter.display import BOLD, CYAN, DIM, GREEN, PINK, RED, WHITE, YELLOW, ago, c, hr
from ccmeter.poll import HEALTH_FILE
from ccmeter.report import BUCKET_LABELS, account_clause, burn_rate


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


def _read_health() -> dict[str, Any] | None:
    if not HEALTH_FILE.exists():
        return None
    try:
        return json.loads(HEALTH_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


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

    creds = get_credentials()
    account_id = fetch_account_id(creds.access_token) if creds else None
    af = account_clause(account_id)

    conn = connect()

    total = conn.execute(f"SELECT COUNT(*) as n FROM usage_samples WHERE {af()}").fetchone()["n"]
    latest = conn.execute(f"SELECT ts FROM usage_samples WHERE {af()} ORDER BY ts DESC LIMIT 1").fetchone()
    oldest = conn.execute(f"SELECT ts FROM usage_samples WHERE {af()} ORDER BY ts ASC LIMIT 1").fetchone()

    # per-bucket current state
    current = conn.execute(
        f"""SELECT bucket, utilization, resets_at, ts FROM usage_samples
           WHERE {af()} AND id IN (SELECT MAX(id) FROM usage_samples WHERE {af()} GROUP BY bucket)
           ORDER BY bucket"""
    ).fetchall()

    # collection gaps: samples in last 24h
    recent_count = conn.execute(
        f"SELECT COUNT(*) as n FROM usage_samples WHERE {af()} AND ts > datetime('now', '-24 hours')"
    ).fetchone()["n"]

    daemon_text, daemon_color = _daemon_status()

    print()
    print(f"  {c(BOLD + WHITE, 'ccmeter status')}")
    print(f"  {hr()}")
    print(f"  {c(DIM, 'daemon')}   {c(daemon_color, daemon_text)}")
    print(f"  {c(DIM, 'db')}       {c(DIM, _db_size())}  {c(DIM, str(DB_PATH))}")
    print(f"  {c(DIM, 'samples')}  {c(WHITE, total)}  {c(DIM, f'({recent_count} last 24h)')}")

    # Daemon health from health.json
    health = _read_health()
    if health and not health.get("ok", True):
        fails = health.get("consecutive_failures", 0)
        errors = health.get("recent_errors", [])
        if errors:
            codes = [str(e.get("status", "?")) for e in errors]
            last_ts = errors[-1].get("ts", "")
            err_age = ago(last_ts) if last_ts else ""
            err_color = RED if fails >= 5 else YELLOW
            print(f"  {c(DIM, 'health')}   {c(err_color, f'{fails} failures')}  {c(DIM, ' '.join(codes))}  {c(DIM, err_age)}")
    if latest:
        freshness = ago(latest["ts"])
        fresh_color = (
            GREEN if "just now" in freshness or "m ago" in freshness else YELLOW if "h ago" in freshness else RED
        )
        print(f"  {c(DIM, 'latest')}   {c(fresh_color, freshness)}")
    if oldest and latest:
        print(f"  {c(DIM, 'range')}    {c(DIM, oldest['ts'][:16])} → {c(DIM, latest['ts'][:16])}")
    print()

    if current:
        # Window sizes for burn rate computation
        window_hours: dict[str, float] = {
            "five_hour": 5.0,
            "seven_day": 168.0,
            "seven_day_opus": 168.0,
            "seven_day_sonnet": 168.0,
            "seven_day_cowork": 168.0,
            "seven_day_oauth_apps": 168.0,
        }

        for r in current:
            util = r["utilization"]
            bucket = r["bucket"]
            color = GREEN if util < 50 else YELLOW if util < 80 else CYAN
            label = BUCKET_LABELS.get(bucket, bucket)
            line = f"    {label:<22} {c(color, f'{util:5.1f}%')}  {c(DIM, ago(r['ts']))}"

            # Burn rate prediction
            resets_at = r["resets_at"]
            wh = window_hours.get(bucket)
            if resets_at and wh and util > 0:
                br = burn_rate(util, resets_at, wh)
                if br:
                    rate = br["rate_pct_per_hour"]
                    mins = br["remaining_mins"]
                    warning = br["warning"]

                    # %/day for weekly buckets, %/h for 5h
                    if wh > 24:
                        rate_str = f"{rate * 24:.0f}%/d"
                    else:
                        rate_str = f"{rate:.0f}%/h"

                    if warning == "critical":
                        line += f"  {c(RED, '▲')} {c(RED, rate_str)}"
                    elif warning == "warning":
                        line += f"  {c(YELLOW, '▲')} {c(YELLOW, rate_str)}"
                    elif rate > 0:
                        line += f"  {c(DIM, rate_str)}"

                    if mins < 60:
                        line += f"  {c(PINK, f'~{mins:.0f}m left')}"
                    elif mins < float("inf"):
                        hours = mins / 60
                        if hours >= 24:
                            line += f"  {c(DIM, f'~{hours / 24:.0f}d left')}"
                        else:
                            line += f"  {c(DIM, f'~{hours:.0f}h left')}"

            # Plateau detection: >95% and no change recently suggests rate-limited
            if util >= 95:
                recent = conn.execute(
                    f"SELECT utilization FROM usage_samples WHERE bucket = ? AND {af()} ORDER BY id DESC LIMIT 3",
                    (bucket,),
                ).fetchall()
                if len(recent) >= 3 and all(abs(row["utilization"] - util) < 1.0 for row in recent):
                    line += f"  {c(RED, 'rate limited')}"

            print(line)
        print()

    conn.close()
