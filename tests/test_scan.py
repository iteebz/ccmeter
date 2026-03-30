"""Test JSONL scanning — line filtering, activity extraction."""

import json
from pathlib import Path

from ccmeter.scan import _scan_file


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    with path.open("w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def test_user_prompts_captured_without_tool_use(tmp_path: Path) -> None:
    """User messages with plain text (no tool_use, no usage) must produce activity events."""
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "timestamp": "2026-03-30T02:00:00Z",
                "type": "user",
                "message": {"content": [{"type": "text", "text": "explain this code"}]},
            },
            {
                "timestamp": "2026-03-30T02:00:01Z",
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "sure"}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                    "model": "claude-opus-4-6",
                },
            },
        ],
    )

    events, activity = _scan_file(jsonl, "2026-03-30T00:00:00Z")
    assert len(events) == 1  # assistant message with usage
    assert len(activity) == 2  # user prompt + assistant turn
    assert activity[0].prompts == 1
    assert activity[1].turns == 1


def test_tool_use_lines_produce_activity(tmp_path: Path) -> None:
    """Lines with tool_use blocks must be parsed for activity."""
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "timestamp": "2026-03-30T02:00:00Z",
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "/foo"}},
                    ],
                    "usage": {"input_tokens": 50, "output_tokens": 20},
                    "model": "claude-opus-4-6",
                },
            },
        ],
    )

    events, activity = _scan_file(jsonl, "2026-03-30T00:00:00Z")
    assert len(events) == 1
    assert len(activity) == 1
    assert activity[0].reads == 1
    assert activity[0].tool_calls == 1


def test_lines_before_cutoff_skipped(tmp_path: Path) -> None:
    """Lines with timestamps before cutoff must not produce events."""
    jsonl = tmp_path / "session.jsonl"
    _write_jsonl(
        jsonl,
        [
            {
                "timestamp": "2026-03-01T00:00:00Z",
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "old"}],
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                    "model": "claude-opus-4-6",
                },
            },
        ],
    )

    events, activity = _scan_file(jsonl, "2026-03-29T00:00:00Z")
    assert len(events) == 0
    assert len(activity) == 0
