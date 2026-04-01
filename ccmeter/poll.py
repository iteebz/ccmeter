"""Poll Anthropic usage API and record samples."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import sys
import time
import types
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from ccmeter.auth import Credentials, fetch_account_id, get_credentials
from ccmeter.config import pinned_account
from ccmeter.db import connect

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

PIDFILE = Path.home() / ".ccmeter" / "poll.pid"
HEALTH_FILE = Path.home() / ".ccmeter" / "health.json"
LOG_DIR = Path.home() / ".ccmeter"
MAX_LOG_BYTES = 512 * 1024  # 512KB

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA_HEADER = "oauth-2025-04-20"

BUCKETS = ("five_hour", "seven_day", "seven_day_sonnet", "seven_day_opus", "seven_day_cowork", "extra_usage")

_MAX_RECENT_ERRORS = 5

_running = True


@dataclass
class PollResult:
    data: dict[str, Any] | None = None
    status: int = 0
    retry_after: int | None = None
    error: str = ""


def _handle_signal(sig: int, frame: types.FrameType | None) -> None:
    global _running
    _running = False
    print("\nshutting down...")


def fetch_usage(creds: Credentials) -> PollResult:
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
        return PollResult(data=json.loads(resp.read().decode()), status=resp.status)
    except urllib.error.HTTPError as e:
        retry_after = None
        ra = e.headers.get("Retry-After") if e.headers else None
        if ra and ra.isdigit():
            retry_after = int(ra)
        return PollResult(status=e.code, retry_after=retry_after, error=str(e))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        return PollResult(error=str(e))


def record_samples(
    data: dict[str, Any],
    last_seen: dict[str, float],
    conn: sqlite3.Connection,
    tier: str | None = None,
    account_id: str | None = None,
) -> dict[str, float]:
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
        if prev is not None and abs(prev - utilization) < 1e-9:
            continue

        resets_at = value.get("resets_at")
        conn.execute(
            "INSERT INTO usage_samples (bucket, utilization, resets_at, tier, raw, account_id) VALUES (?, ?, ?, ?, ?, ?)",
            (key, float(utilization), resets_at, tier, json.dumps(value), account_id),
        )
        conn.commit()

        direction = ""
        if prev is not None:
            direction = f" (was {prev}%)"
        print(f"  {key}: {utilization}%{direction}")

        last_seen[key] = utilization

    return last_seen


def seed_last_seen(conn: sqlite3.Connection, account_id: str | None = None) -> dict[str, float]:
    """Load most recent utilization per bucket from DB to avoid duplicate rows on restart."""
    last_seen = {}
    if account_id:
        rows = conn.execute(
            "SELECT bucket, utilization FROM usage_samples "
            "WHERE account_id = ? AND id IN (SELECT MAX(id) FROM usage_samples WHERE account_id = ? GROUP BY bucket)",
            (account_id, account_id),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT bucket, utilization FROM usage_samples WHERE id IN (SELECT MAX(id) FROM usage_samples GROUP BY bucket)"
        ).fetchall()
    for row in rows:
        last_seen[row["bucket"]] = row["utilization"]
    return last_seen


def _next_delay(result: PollResult, interval: int, backoff: int) -> int:
    """Decide how long to wait before next poll based on failure type."""
    if result.data:
        return interval

    # 429: respect Retry-After fully, or use interval as floor
    if result.status == 429:
        if result.retry_after:
            return result.retry_after
        return max(interval, 60)

    # 401/403: cred refresh will happen separately, short retry
    if result.status in (401, 403):
        return 30

    # network/server errors: exponential backoff capped at 5m
    return min(backoff * 2, 300)


def _acquire_lock() -> IO[str]:
    """Acquire exclusive pidfile lock. Returns file handle or exits."""
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
    f = PIDFILE.open("w")
    try:
        if sys.platform == "win32":
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("error: another ccmeter poll is already running", file=sys.stderr)
        f.close()
        sys.exit(1)
    f.write(str(os.getpid()))
    f.flush()
    return f


def _write_health(
    ok: bool,
    interval: int,
    consecutive_failures: int,
    recent_errors: list[dict[str, Any]],
) -> None:
    """Atomically write daemon health to a JSON file. Single snapshot, no history."""
    health = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "ok": ok,
        "interval": interval,
        "consecutive_failures": consecutive_failures,
        "recent_errors": recent_errors[-_MAX_RECENT_ERRORS:],
    }
    tmp = HEALTH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(health))
    tmp.rename(HEALTH_FILE)


def _rotate_logs() -> None:
    """Truncate log files if they exceed MAX_LOG_BYTES."""
    for name in ("poll.log", "poll.err"):
        log = LOG_DIR / name
        if log.exists() and log.stat().st_size > MAX_LOG_BYTES:
            # Keep the last 64KB
            data = log.read_bytes()[-65536:]
            log.write_bytes(data)


def run_poll(interval: int = 120, once: bool = False):
    """Main poll loop."""
    lock = _acquire_lock()
    _rotate_logs()
    creds = get_credentials()
    if not creds:
        print("error: could not find Claude Code OAuth token in OS keychain", file=sys.stderr)
        print(file=sys.stderr)
        print("ccmeter reads the same credential Claude Code uses.", file=sys.stderr)
        print("make sure Claude Code is installed and you've signed in.", file=sys.stderr)
        sys.exit(1)

    tier = creds.subscription_type or creds.rate_limit_tier
    account_id = fetch_account_id(creds.access_token)
    pinned = pinned_account()
    conn = connect()
    last_seen = seed_last_seen(conn, account_id=account_id)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    print(f"ccmeter polling every {interval}s")
    if account_id:
        print(f"  account: {account_id[:8]}…")
    if pinned:
        if pinned == account_id:
            print(f"  pinned: {pinned[:8]}… (match)")
        else:
            print(f"  pinned: {pinned[:8]}… (mismatch — will skip)")
    if tier:
        print(f"  tier: {tier}")
    if last_seen:
        print(f"  resumed with {len(last_seen)} cached bucket(s)")

    backoff = interval
    consecutive_failures = 0
    recent_errors: list[dict[str, Any]] = []
    account_dirty = False  # true after cred refresh; resolve on next successful fetch
    while _running:
        result = fetch_usage(creds)
        if result.data:
            if account_dirty:
                new_account = fetch_account_id(creds.access_token)
                if new_account and new_account != account_id:
                    account_id = new_account
                    last_seen = seed_last_seen(conn, account_id=account_id)
                    print(f"  account changed: {account_id[:8]}…")
                account_dirty = False
            if pinned and account_id != pinned:
                pass  # skip — wrong account
            else:
                last_seen = record_samples(result.data, last_seen, conn, tier=tier, account_id=account_id)
            backoff = interval
            consecutive_failures = 0
            recent_errors.clear()
            _write_health(True, interval, 0, [])
        else:
            consecutive_failures += 1
            recent_errors.append({
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "status": result.status,
                "error": (result.error[:200] if result.error else ""),
            })
            if len(recent_errors) > _MAX_RECENT_ERRORS:
                recent_errors = recent_errors[-_MAX_RECENT_ERRORS:]

            # auth failures: refresh creds and retry immediately if token changed
            if result.status in (401, 403):
                refreshed = get_credentials()
                if refreshed and refreshed.access_token != creds.access_token:
                    creds = refreshed
                    tier = creds.subscription_type or creds.rate_limit_tier
                    account_dirty = True
                    print("  refreshed credentials")
                    consecutive_failures = 0
                    if not once:
                        continue  # retry now with fresh creds
            elif consecutive_failures >= 3:
                refreshed = get_credentials()
                if refreshed:
                    creds = refreshed
                    tier = creds.subscription_type or creds.rate_limit_tier
                    account_dirty = True
                    print("  refreshed credentials (fallback)")
                    consecutive_failures = 0

            _write_health(False, interval, consecutive_failures, recent_errors)

            delay = _next_delay(result, interval, backoff)
            backoff = delay
            label = f" [{result.status}]" if result.status else ""
            print(f"  retry in {delay}s{label}", file=sys.stderr)

        if once:
            break

        time.sleep(backoff)

    conn.close()
    lock.close()
    print("stopped.")
