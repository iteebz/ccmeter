"""Test calibration math."""

import sqlite3
from pathlib import Path

from ccmeter.migrations import migrate
from ccmeter.report import calibrate_bucket, tokens_in_window
from ccmeter.scan import TokenEvent


def _make_event(
    ts: str, model: str = "claude-opus-4-6", input_tokens: int = 100,
    output_tokens: int = 50, cache_read: int = 1000, cache_create: int = 200,
) -> TokenEvent:
    return TokenEvent(
        ts=ts,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read=cache_read,
        cache_create=cache_create,
        model=model,
        session_id="test",
        cc_version="2.1.77",
    )


def test_tokens_in_window_filters_by_timestamp():
    events = [
        _make_event("2026-03-30T01:00:00Z", input_tokens=100),
        _make_event("2026-03-30T02:00:00Z", input_tokens=200),
        _make_event("2026-03-30T03:00:00Z", input_tokens=300),
    ]
    result = tokens_in_window(events, "2026-03-30T01:30:00Z", "2026-03-30T02:30:00Z")
    assert result["claude-opus-4-6"]["input"] == 200


def test_tokens_in_window_groups_by_model():
    events = [
        _make_event("2026-03-30T02:00:00Z", model="claude-opus-4-6", input_tokens=100),
        _make_event("2026-03-30T02:00:00Z", model="claude-sonnet-4-6", input_tokens=500),
    ]
    result = tokens_in_window(events, "2026-03-30T01:00:00Z", "2026-03-30T03:00:00Z")
    assert result["claude-opus-4-6"]["input"] == 100
    assert result["claude-sonnet-4-6"]["input"] == 500


def test_calibrate_bucket_computes_tokens_per_pct(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    migrate(conn)

    # insert two usage samples: 10% -> 12% (delta of 2%)
    conn.execute(
        "INSERT INTO usage_samples (ts, bucket, utilization, tier) VALUES (?, ?, ?, ?)",
        ("2026-03-30T02:00:00Z", "five_hour", 10.0, "max"),
    )
    conn.execute(
        "INSERT INTO usage_samples (ts, bucket, utilization, tier) VALUES (?, ?, ?, ?)",
        ("2026-03-30T02:15:00Z", "five_hour", 12.0, "max"),
    )
    conn.commit()

    # 2000 input tokens in that window, delta is 2%, so 1000 per pct
    events = [
        _make_event("2026-03-30T02:05:00Z", input_tokens=1000, output_tokens=0, cache_read=0, cache_create=0),
        _make_event("2026-03-30T02:10:00Z", input_tokens=1000, output_tokens=0, cache_read=0, cache_create=0),
    ]

    cals = calibrate_bucket("five_hour", events, conn)
    assert len(cals) == 1
    assert cals[0]["delta_pct"] == 2.0
    assert cals[0]["models"]["claude-opus-4-6"]["tokens_per_pct"]["input"] == 1000
    assert cals[0]["models"]["claude-opus-4-6"]["total_per_pct"] == 1000
    conn.close()


def test_calibrate_skips_decreasing_utilization(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    migrate(conn)

    # utilization went down (reset) — should not produce a calibration point
    conn.execute(
        "INSERT INTO usage_samples (ts, bucket, utilization) VALUES (?, ?, ?)",
        ("2026-03-30T02:00:00Z", "five_hour", 90.0),
    )
    conn.execute(
        "INSERT INTO usage_samples (ts, bucket, utilization) VALUES (?, ?, ?)",
        ("2026-03-30T07:00:00Z", "five_hour", 5.0),
    )
    conn.commit()

    events = [_make_event("2026-03-30T03:00:00Z")]
    cals = calibrate_bucket("five_hour", events, conn)
    assert len(cals) == 0
    conn.close()
