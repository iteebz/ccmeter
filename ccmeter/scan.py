"""Scan Claude Code JSONL files for per-message token usage."""

import json
import platform
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude" / "projects"


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
    cc_versions: set[str] = field(default_factory=set)
    models: set[str] = field(default_factory=set)
    sessions: int = 0
    os: str = field(default_factory=lambda: platform.system().lower())


def scan(days: int = 30) -> ScanResult:
    """Scan all JSONL files for token events within the lookback window."""
    cutoff = (datetime.now(tz=UTC) - timedelta(days=days)).isoformat()
    result = ScanResult()
    seen_sessions = set()

    if not CLAUDE_DIR.exists():
        return result

    for jsonl in CLAUDE_DIR.glob("*/*.jsonl"):
        _scan_file(jsonl, cutoff, result, seen_sessions)

    result.sessions = len(seen_sessions)
    result.events.sort(key=lambda e: e.ts)
    return result


def _scan_file(path: Path, cutoff: str, result: ScanResult, seen_sessions: set):
    try:
        with path.open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = d.get("message")
                if not isinstance(msg, dict) or "usage" not in msg:
                    continue

                ts = d.get("timestamp", "")
                if ts < cutoff:
                    continue

                usage = msg["usage"]
                session_id = d.get("sessionId", "")
                cc_version = d.get("version", "")
                model = msg.get("model", "")

                if session_id:
                    seen_sessions.add(session_id)
                if cc_version:
                    result.cc_versions.add(cc_version)
                if model:
                    result.models.add(model)

                result.events.append(
                    TokenEvent(
                        ts=ts,
                        input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0),
                        cache_read=usage.get("cache_read_input_tokens", 0),
                        cache_create=usage.get("cache_creation_input_tokens", 0),
                        model=model,
                        session_id=session_id,
                        cc_version=cc_version,
                    )
                )
    except OSError:
        pass
