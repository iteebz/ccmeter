"""Poll Anthropic usage API and record samples."""

import json
import signal
import sys
import time

import httpx

from ccmeter.auth import get_oauth_token
from ccmeter.db import connect

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"

# Buckets we track
BUCKETS = ("five_hour", "seven_day", "seven_day_sonnet", "seven_day_opus", "seven_day_cowork", "extra_usage")

_running = True


def _handle_signal(sig, frame):
    global _running
    _running = False
    print("\nshutting down...")


def fetch_usage(token: str) -> dict | None:
    """Fetch current usage from Anthropic's OAuth endpoint."""
    try:
        resp = httpx.get(
            USAGE_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"usage API returned {resp.status_code}", file=sys.stderr)
            return None
        return resp.json()
    except httpx.HTTPError as e:
        print(f"usage API error: {e}", file=sys.stderr)
        return None


def record_samples(data: dict, last_seen: dict, conn) -> dict:
    """Write rows for any bucket that changed. Returns updated last_seen."""
    for bucket in BUCKETS:
        info = data.get(bucket)
        if info is None:
            continue

        utilization = info.get("utilization")
        if utilization is None:
            continue

        prev = last_seen.get(bucket)
        if prev is not None and prev == utilization:
            continue

        resets_at = info.get("resets_at")
        conn.execute(
            "INSERT INTO usage_samples (bucket, utilization, resets_at, raw) VALUES (?, ?, ?, ?)",
            (bucket, utilization, resets_at, json.dumps(info)),
        )
        conn.commit()

        direction = ""
        if prev is not None:
            direction = f" (was {prev}%)"
        print(f"  {bucket}: {utilization}%{direction}")

        last_seen[bucket] = utilization

    return last_seen


def seed_last_seen(conn) -> dict:
    """Load most recent utilization per bucket from DB to avoid duplicate rows on restart."""
    last_seen = {}
    for bucket in BUCKETS:
        row = conn.execute(
            "SELECT utilization FROM usage_samples WHERE bucket = ? ORDER BY ts DESC LIMIT 1",
            (bucket,),
        ).fetchone()
        if row:
            last_seen[bucket] = row["utilization"]
    return last_seen


def run_poll(interval: int = 120, once: bool = False):
    """Main poll loop."""
    token = get_oauth_token()
    if not token:
        print("error: could not find Claude Code OAuth token in OS keychain", file=sys.stderr)
        print("", file=sys.stderr)
        print("cc-meter reads the same credential Claude Code uses.", file=sys.stderr)
        print("make sure Claude Code is installed and you've signed in.", file=sys.stderr)
        sys.exit(1)

    conn = connect()
    last_seen = seed_last_seen(conn)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    print(f"cc-meter polling every {interval}s")
    if last_seen:
        print(f"  resumed with {len(last_seen)} cached bucket(s)")

    backoff = interval
    while _running:
        data = fetch_usage(token)
        if data:
            last_seen = record_samples(data, last_seen, conn)
            backoff = interval
        else:
            backoff = min(backoff * 2, 600)
            print(f"  backing off to {backoff}s", file=sys.stderr)

        if once:
            break

        time.sleep(backoff)

    conn.close()
    print("stopped.")
