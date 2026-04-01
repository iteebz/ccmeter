"""Activity metrics: tool calls, prompts, LOC — extracted during scan, windowed later."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, TypedDict

READ_TOOLS = {"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch"}
WRITE_TOOLS = {"Edit", "MultiEdit", "Write"}
BASH_TOOLS = {"Bash"}


def count_lines(s: str) -> int:
    return s.count("\n") + (1 if s and not s.endswith("\n") else 0)


@dataclass
class ActivityEvent:
    ts: str
    prompts: int = 0
    turns: int = 0
    tool_calls: int = 0
    reads: int = 0
    writes: int = 0
    bash: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    tool_name: str = ""
    model: str = ""


def extract_activity(d: dict[str, Any], msg_type: str, msg: dict[str, Any]) -> ActivityEvent | None:
    """Extract activity from an already-parsed JSONL line. Called during scan."""
    ts = d.get("timestamp", "")
    if not ts:
        return None

    ev = ActivityEvent(ts=ts)
    hit = False

    if msg_type == "user":
        content = msg.get("content", [])
        is_prompt = False
        if isinstance(content, str):
            is_prompt = bool(content.strip())
        else:
            is_prompt = any(
                isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip() for b in content
            )
        if is_prompt:
            ev.prompts = 1
            hit = True

    if msg_type == "assistant":
        ev.turns = 1
        hit = True

    for block in msg.get("content", []):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name", "unknown")
        inp = block.get("input", {})
        ev.tool_calls += 1
        ev.tool_name = name
        hit = True
        if name in READ_TOOLS:
            ev.reads += 1
        if name in WRITE_TOOLS:
            ev.writes += 1
            if name == "Edit":
                ev.lines_added += count_lines(inp.get("new_string", ""))
                ev.lines_removed += count_lines(inp.get("old_string", ""))
            elif name == "MultiEdit":
                for edit in inp.get("edits", []):
                    ev.lines_added += count_lines(edit.get("new_string", ""))
                    ev.lines_removed += count_lines(edit.get("old_string", ""))
            elif name == "Write":
                ev.lines_added += count_lines(inp.get("content", ""))
        if name in BASH_TOOLS:
            ev.bash += 1

    return ev if hit else None


class ActivityWindow(TypedDict):
    prompts: int
    turns: int
    tool_calls: int
    reads: int
    writes: int
    bash: int
    lines_added: int
    lines_removed: int
    tools: dict[str, int]


def activity_in_window(events: list[ActivityEvent], t0: str, t1: str) -> ActivityWindow:
    """Sum activity metrics for events between two timestamps."""
    import bisect

    lo = bisect.bisect_left(events, t0, key=lambda e: e.ts)
    hi = bisect.bisect_right(events, t1, key=lambda e: e.ts)
    prompts = 0
    turns = 0
    tool_calls = 0
    reads = 0
    writes = 0
    bash = 0
    lines_added = 0
    lines_removed = 0
    tools: Counter[str] = Counter()
    for i in range(lo, hi):
        e = events[i]
        prompts += e.prompts
        turns += e.turns
        tool_calls += e.tool_calls
        reads += e.reads
        writes += e.writes
        bash += e.bash
        lines_added += e.lines_added
        lines_removed += e.lines_removed
        if e.tool_name:
            tools[e.tool_name] += 1
    return {
        "prompts": prompts,
        "turns": turns,
        "tool_calls": tool_calls,
        "reads": reads,
        "writes": writes,
        "bash": bash,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "tools": dict(tools),
    }


def activity_in_window_by_model(events: list[ActivityEvent], t0: str, t1: str) -> dict[str, ActivityWindow]:
    """Sum activity metrics per model for events between two timestamps."""
    import bisect
    from collections import defaultdict

    lo = bisect.bisect_left(events, t0, key=lambda e: e.ts)
    hi = bisect.bisect_right(events, t1, key=lambda e: e.ts)
    by_model: dict[str, list[ActivityEvent]] = defaultdict(list)
    for i in range(lo, hi):
        e = events[i]
        key = e.model or "unknown"
        by_model[key].append(e)

    result: dict[str, ActivityWindow] = {}
    for model, model_events in by_model.items():
        prompts = 0
        turns = 0
        tool_calls = 0
        reads = 0
        writes = 0
        bash = 0
        lines_added = 0
        lines_removed = 0
        tools: Counter[str] = Counter()
        for e in model_events:
            prompts += e.prompts
            turns += e.turns
            tool_calls += e.tool_calls
            reads += e.reads
            writes += e.writes
            bash += e.bash
            lines_added += e.lines_added
            lines_removed += e.lines_removed
            if e.tool_name:
                tools[e.tool_name] += 1
        result[model] = {
            "prompts": prompts,
            "turns": turns,
            "tool_calls": tool_calls,
            "reads": reads,
            "writes": writes,
            "bash": bash,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "tools": dict(tools),
        }
    return result
