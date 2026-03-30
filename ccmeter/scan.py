"""Scan Claude Code JSONL files for per-message token usage and activity."""

import json
import os
import platform
import sqlite3
import sys
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ccmeter.activity import ActivityEvent, extract_activity
from ccmeter.db import connect
from ccmeter.display import progress, progress_done

CLAUDE_DIR = Path.home() / ".claude" / "projects"

# Bump when parse logic changes to auto-invalidate cache.
CACHE_VERSION = 3


@dataclass
class TokenEvent:
    ts: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_create: int
    model: str
    session_id: str
    cc_version: str


@dataclass
class ScanResult:
    events: list[TokenEvent] = field(default_factory=list)
    activity: list[ActivityEvent] = field(default_factory=list)
    cc_versions: set[str] = field(default_factory=set)
    models: set[str] = field(default_factory=set)
    sessions: int = 0
    os: str = field(default_factory=lambda: platform.system().lower())


def _token_to_dict(e: TokenEvent) -> dict[str, Any]:
    return {
        "ts": e.ts,
        "in": e.input_tokens,
        "out": e.output_tokens,
        "cr": e.cache_read,
        "cc": e.cache_create,
        "m": e.model,
        "s": e.session_id,
        "v": e.cc_version,
    }


def _dict_to_token(d: dict[str, Any]) -> TokenEvent:
    return TokenEvent(
        ts=d["ts"],
        input_tokens=d["in"],
        output_tokens=d["out"],
        cache_read=d["cr"],
        cache_create=d["cc"],
        model=d["m"],
        session_id=d["s"],
        cc_version=d["v"],
    )


def _activity_to_dict(e: ActivityEvent) -> dict[str, Any]:
    return {
        "ts": e.ts,
        "tc": e.tool_calls,
        "r": e.reads,
        "w": e.writes,
        "b": e.bash,
        "la": e.lines_added,
        "lr": e.lines_removed,
        "tn": e.tool_name,
        "p": e.prompts,
        "t": e.turns,
    }


def _dict_to_activity(d: dict[str, Any]) -> ActivityEvent:
    return ActivityEvent(
        ts=d["ts"],
        tool_calls=d.get("tc", 0),
        reads=d.get("r", 0),
        writes=d.get("w", 0),
        bash=d.get("b", 0),
        lines_added=d.get("la", 0),
        lines_removed=d.get("lr", 0),
        tool_name=d.get("tn", ""),
        prompts=d.get("p", 0),
        turns=d.get("t", 0),
    )


def scan(days: int = 30, recache: bool = False) -> ScanResult:
    """Scan all JSONL files for token events and activity within the lookback window."""
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
    result = ScanResult()
    seen_sessions = set()

    if not CLAUDE_DIR.exists():
        return result

    cutoff_ts = (datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp()
    file_stats: list[tuple[Path, os.stat_result]] = []
    for f in CLAUDE_DIR.glob("*/*.jsonl"):
        st = f.stat()
        if st.st_mtime >= cutoff_ts:
            file_stats.append((f, st))

    tty = sys.stdout.isatty()
    total = len(file_stats)

    conn = connect()

    # Prune cache entries for files outside the lookback window
    _prune_cache(conn, {str(f) for f, _ in file_stats})
    if recache:
        conn.execute("DELETE FROM scan_cache")
        conn.commit()
        cache = {}
    else:
        cache = _load_cache(conn)

    if tty and total:
        progress(total, 0, "scan")

    new_cache: list[tuple[str, float, int, int, bytes, bytes]] = []
    for i, (jsonl, st) in enumerate(file_stats):
        key = str(jsonl)
        cached = cache.get(key)

        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            events, activity = cached[2], cached[3]
        else:
            events, activity = scan_file(jsonl, cutoff)
            new_cache.append(
                (
                    key,
                    st.st_mtime,
                    st.st_size,
                    CACHE_VERSION,
                    zlib.compress(json.dumps([_token_to_dict(e) for e in events]).encode()),
                    zlib.compress(json.dumps([_activity_to_dict(a) for a in activity]).encode()),
                )
            )

        for e in events:
            if e.ts >= cutoff:
                result.events.append(e)
                if e.session_id:
                    seen_sessions.add(e.session_id)
                if e.cc_version:
                    result.cc_versions.add(e.cc_version)
                if e.model:
                    result.models.add(e.model)

        for a in activity:
            if a.ts >= cutoff:
                result.activity.append(a)

        if tty and total:
            progress(total, i + 1, "scan")

    if new_cache:
        _save_cache(conn, new_cache)
    conn.close()

    if tty and total:
        progress_done("scan")

    result.sessions = len(seen_sessions)
    result.events.sort(key=lambda e: e.ts)
    result.activity.sort(key=lambda e: e.ts)
    return result


def _load_cache(conn: sqlite3.Connection) -> dict[str, tuple[float, int, list[TokenEvent], list[ActivityEvent]]]:
    """Load scan cache into memory. Returns {path: (mtime, size, events, activity)}."""
    # Invalidate if cache version changed
    stale = conn.execute("SELECT COUNT(*) FROM scan_cache WHERE version != ?", (CACHE_VERSION,)).fetchone()[0]
    if stale:
        conn.execute("DELETE FROM scan_cache")
        conn.commit()
        conn.execute("VACUUM")
        return {}

    rows = conn.execute("SELECT path, mtime, size, events, activity FROM scan_cache").fetchall()
    cache = {}
    for row in rows:
        try:
            raw_events = row["events"]
            raw_activity = row["activity"]
            # Decompress if stored as bytes (v3+), otherwise parse as JSON string (v2)
            if isinstance(raw_events, bytes):
                events_json = zlib.decompress(raw_events).decode()
                activity_json = zlib.decompress(raw_activity).decode()
            else:
                events_json = raw_events
                activity_json = raw_activity
            events = [_dict_to_token(d) for d in json.loads(events_json)]
            activity = [_dict_to_activity(d) for d in json.loads(activity_json)]
            cache[row["path"]] = (row["mtime"], row["size"], events, activity)
        except Exception:  # noqa: S112, PERF203
            continue
    return cache


def _prune_cache(conn: sqlite3.Connection, active_paths: set[str]) -> None:
    """Remove cache entries for files no longer in the lookback window."""
    cached = {row[0] for row in conn.execute("SELECT path FROM scan_cache").fetchall()}
    stale = cached - active_paths
    if stale:
        conn.executemany("DELETE FROM scan_cache WHERE path = ?", [(p,) for p in stale])
        conn.commit()
        conn.execute("VACUUM")


def _save_cache(conn: sqlite3.Connection, entries: list[tuple[str, float, int, int, bytes, bytes]]) -> None:
    """Write new/updated cache entries."""
    conn.executemany(
        "INSERT OR REPLACE INTO scan_cache (path, mtime, size, version, events, activity) VALUES (?, ?, ?, ?, ?, ?)",
        entries,
    )
    conn.commit()


def scan_file(path: Path, cutoff: str) -> tuple[list[TokenEvent], list[ActivityEvent]]:
    events = []
    activity = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"usage"' not in line and '"tool_use"' not in line and '"user"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = d.get("timestamp", "")
                if not ts or ts < cutoff:
                    continue

                msg_type = d.get("type", "")
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue

                usage = msg.get("usage")
                model = msg.get("model", "")
                if usage and model and not model.startswith("<"):
                    events.append(
                        TokenEvent(
                            ts=ts,
                            input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0),
                            cache_read=usage.get("cache_read_input_tokens", 0),
                            cache_create=usage.get("cache_creation_input_tokens", 0),
                            model=model,
                            session_id=d.get("sessionId", ""),
                            cc_version=d.get("version", ""),
                        )
                    )

                act = extract_activity(d, msg_type, msg)
                if act:
                    activity.append(act)
    except OSError:
        pass
    return events, activity
