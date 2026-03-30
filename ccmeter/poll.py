"""Poll Anthropic usage API and record samples."""

import json
import signal
import sqlite3
import sys
import time
import types
import urllib.error
import urllib.request
from typing import Any

from ccmeter.auth import Credentials, get_credentials
from ccmeter.db import connect

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"

BUCKETS = ("five_hour", "seven_day", "seven_day_sonnet", "seven_day_opus", "seven_day_cowork", "extra_usage")

_running = True


def _handle_signal(sig: int, frame: types.FrameType | None) -> None:
    global _running
    _running = False
    print("\nshutting down...")


def fetch_usage(creds: Credentials) -> dict[str, Any] | None:
    """Fetch current usage from Anthropic's OAuth endpoint."""
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {creds.access_token}",
            "anthropic-beta": BETA_HEADER,
        },
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"usage API error: {e}", file=sys.stderr)
        return None


def record_samples(data: dict[str, Any], last_seen: dict[str, float], conn: sqlite3.Connection, tier: str | None = None) -> dict[str, float]:
    """Write rows for any bucket that changed. Returns updated last_seen."""
    for key, value in data.items():
        if not isinstance(value, dict):
            continue

        utilization = value.get("utilization")
        if utilization is None and key == "extra_usage":
            utilization = value.get("used_credits")
        if utilization is None:
            continue

        prev = last_seen.get(key)
        if prev is not None and prev == utilization:
            continue

        resets_at = value.get("resets_at")
        conn.execute(
            "INSERT INTO usage_samples (bucket, utilization, resets_at, tier, raw) VALUES (?, ?, ?, ?, ?)",
            (key, float(utilization), resets_at, tier, json.dumps(value)),
        )
        conn.commit()

        direction = ""
        if prev is not None:
            direction = f" (was {prev}%)"
        print(f"  {key}: {utilization}%{direction}")

        last_seen[key] = utilization

    return last_seen


def seed_last_seen(conn: sqlite3.Connection) -> dict[str, float]:
    """Load most recent utilization per bucket from DB to avoid duplicate rows on restart."""
    last_seen = {}
    rows = conn.execute(
        "SELECT bucket, utilization FROM usage_samples WHERE id IN (SELECT MAX(id) FROM usage_samples GROUP BY bucket)"
    ).fetchall()
    for row in rows:
        last_seen[row["bucket"]] = row["utilization"]
    return last_seen


def run_poll(interval: int = 120, once: bool = False):
    """Main poll loop."""
    creds = get_credentials()
    if not creds:
        print("error: could not find Claude Code OAuth token in OS keychain", file=sys.stderr)
        print(file=sys.stderr)
        print("ccmeter reads the same credential Claude Code uses.", file=sys.stderr)
        print("make sure Claude Code is installed and you've signed in.", file=sys.stderr)
        sys.exit(1)

    tier = creds.subscription_type or creds.rate_limit_tier
    conn = connect()
    last_seen = seed_last_seen(conn)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    print(f"ccmeter polling every {interval}s")
    if tier:
        print(f"  tier: {tier}")
    if last_seen:
        print(f"  resumed with {len(last_seen)} cached bucket(s)")

    backoff = interval
    while _running:
        data = fetch_usage(creds)
        if data:
            last_seen = record_samples(data, last_seen, conn, tier=tier)
            backoff = interval
        else:
            backoff = min(backoff * 2, 600)
            print(f"  backing off to {backoff}s", file=sys.stderr)

        if once:
            break

        time.sleep(backoff)

    conn.close()
    print("stopped.")
