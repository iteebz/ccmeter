"""Generate calibration report by cross-referencing usage ticks against JSONL token data."""

import json
import sqlite3
import sys
from collections import defaultdict
from typing import Any

from ccmeter import __version__
from ccmeter.activity import ActivityEvent, activity_in_window
from ccmeter.auth import get_credentials
from ccmeter.db import connect
from ccmeter.scan import scan

# API pricing per MTok (USD). Used to compute cost-equivalent metrics.
# Source: anthropic.com/pricing as of 2026-03.
# Models not listed here fall back to the most expensive tier.
PRICING = {
    "claude-opus-4-6": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_create": 6.25},
    "claude-sonnet-4-6": {"input": 1.50, "output": 7.50, "cache_read": 0.15, "cache_create": 1.875},
    "claude-haiku-4-5": {"input": 0.40, "output": 2.00, "cache_read": 0.04, "cache_create": 0.50},
}

FALLBACK_PRICING = PRICING["claude-opus-4-6"]


def _pricing_for(model: str) -> dict[str, float]:
    for prefix, rates in PRICING.items():
        if model.startswith(prefix):
            return rates
    return FALLBACK_PRICING


def _cost_usd(tokens: dict[str, int], model: str) -> float:
    """Compute API-equivalent cost in USD for a token breakdown."""
    rates = _pricing_for(model)
    return sum(tokens.get(k, 0) * rates.get(k, 0) / 1_000_000 for k in ("input", "output", "cache_read", "cache_create"))


def _pl(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def tokens_in_window(events: list[Any], t0: str, t1: str) -> dict[str, dict[str, int]]:
    """Sum token counts per model for events between two timestamps."""
    by_model = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0, "count": 0})
    for e in events:
        if t0 <= e.ts <= t1:
            m = e.model or "unknown"
            by_model[m]["input"] += e.input_tokens
            by_model[m]["output"] += e.output_tokens
            by_model[m]["cache_read"] += e.cache_read
            by_model[m]["cache_create"] += e.cache_create
            by_model[m]["count"] += 1
    return dict(by_model)


def calibrate_bucket(bucket: str, events: list[Any], conn: sqlite3.Connection, activity_events: list[ActivityEvent] | None = None) -> list[dict[str, Any]]:
    """Find utilization ticks and calculate tokens per percent per model."""
    rows = conn.execute(
        """
        SELECT s1.ts as t0, s2.ts as t1,
               s1.utilization as u0, s2.utilization as u1,
               s2.utilization - s1.utilization as delta_pct
        FROM usage_samples s1
        JOIN usage_samples s2
            ON s2.bucket = s1.bucket
            AND s2.id = (SELECT MIN(id) FROM usage_samples
                         WHERE bucket = s1.bucket AND id > s1.id)
        WHERE s1.bucket = ?
            AND s2.utilization > s1.utilization
        ORDER BY s1.ts
        """,
        (bucket,),
    ).fetchall()

    calibrations = []
    for r in rows:
        t0, t1, delta = r["t0"], r["t1"], r["delta_pct"]
        by_model = tokens_in_window(events, t0, t1)
        if not by_model:
            continue

        models = {}
        for model, tokens in by_model.items():
            total = tokens["input"] + tokens["output"] + tokens["cache_read"] + tokens["cache_create"]
            tpp = {k: int(v / delta) for k, v in tokens.items() if k != "count"}
            cost = _cost_usd(tpp, model)
            models[model] = {
                "tokens": dict(tokens),
                "tokens_per_pct": tpp,
                "total_per_pct": int(total / delta),
                "cost_per_pct": cost,
                "message_count": tokens["count"],
            }

        activity = None
        if activity_events:
            activity = activity_in_window(activity_events, t0, t1)

        calibrations.append(
            {
                "t0": t0,
                "t1": t1,
                "delta_pct": delta,
                "models": models,
                "mixed": len(models) > 1,
                "activity": activity,
            }
        )
    return calibrations


def run_report(days: int = 30, json_output: bool = False):
    """Generate and display calibration report."""
    creds = get_credentials()
    tier = "unknown"
    rate_tier = "unknown"
    if creds:
        tier = creds.subscription_type or "unknown"
        rate_tier = creds.rate_limit_tier or "unknown"

    result = scan(days=days)

    if not result.events:
        print(f"no token events found in the last {days} days.")
        print("make sure Claude Code has been used and JSONL logs exist in ~/.claude/projects/")
        return

    conn = connect()
    sample_count = conn.execute("SELECT COUNT(*) as n FROM usage_samples").fetchone()["n"]

    if sample_count == 0:
        print("no usage samples collected yet. run: ccmeter poll")
        conn.close()
        return

    buckets = ["five_hour", "seven_day", "seven_day_sonnet"]
    report_data: dict[str, Any] = {
        "version": __version__,
        "tier": tier,
        "rate_limit_tier": rate_tier,
        "os": result.os,
        "cc_versions": sorted(result.cc_versions),
        "models_seen": sorted(result.models),
        "sessions": result.sessions,
        "token_events": len(result.events),
        "usage_samples": sample_count,
        "lookback_days": days,
        "buckets": {},
    }

    for bucket in buckets:
        cals = calibrate_bucket(bucket, result.events, conn, activity_events=result.activity)
        if not cals:
            continue

        model_agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"ticks": 0, "total_per_pct": [], "cost_per_pct": []})
        activity_agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"ticks": 0, "values": []})
        for cal in cals:
            for model, data in cal["models"].items():
                model_agg[model]["ticks"] += 1
                model_agg[model]["total_per_pct"].append(data["total_per_pct"])
                model_agg[model]["cost_per_pct"].append(data["cost_per_pct"])
                for k in ("input", "output", "cache_read", "cache_create"):
                    model_agg[model].setdefault(f"{k}_per_pct", []).append(data["tokens_per_pct"][k])
            if cal.get("activity"):
                act = cal["activity"]
                delta = cal["delta_pct"]
                for k in ("prompts", "turns", "tool_calls", "reads", "writes", "bash", "lines_added", "lines_removed"):
                    activity_agg[k]["ticks"] += 1
                    activity_agg[k]["values"].append(act[k] / delta)

        model_summary = {}
        for model, agg in model_agg.items():
            n = agg["ticks"]
            model_summary[model] = {
                "ticks": n,
                "avg_total_per_pct": int(sum(agg["total_per_pct"]) / n),
                "avg_cost_per_pct": sum(agg["cost_per_pct"]) / n,
                "avg_per_pct": {
                    k: int(sum(agg[f"{k}_per_pct"]) / n) for k in ("input", "output", "cache_read", "cache_create")
                },
            }

        activity_summary = {}
        for k, agg in activity_agg.items():
            n = agg["ticks"]
            if n:
                activity_summary[k] = round(sum(agg["values"]) / n, 1)

        mixed_count = sum(1 for c in cals if c["mixed"])
        report_data["buckets"][bucket] = {
            "ticks": len(cals),
            "mixed_ticks": mixed_count,
            "models": model_summary,
            "activity_per_pct": activity_summary,
        }

    conn.close()

    if json_output:
        print(json.dumps(report_data, indent=2))
        return

    _print_report(report_data)


_DIM = "\033[2m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_WHITE = "\033[37m"
_PURPLE = "\033[38;2;160;130;220m"
_PINK = "\033[38;2;210;140;190m"
_RESET = "\033[0m"
_RULE = "\033[38;2;60;60;80m"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if sys.stdout.isatty() else str(text)


def _hr(width: int = 50) -> str:
    return _c(_RULE, "─" * width)


def _print_report(data: dict[str, Any]) -> None:
    print()
    print(f"  {_c(_BOLD + _WHITE, 'ccmeter')} {_c(_DIM, f'v{data.get("version", "?")}')}    {_c(_PINK, data['tier'])} {_c(_DIM, data['rate_limit_tier'])}")
    print(f"  {_c(_DIM, f'{data["sessions"]:,} sessions  ·  {data["token_events"]:,} events  ·  {data["usage_samples"]} samples  ·  {data["lookback_days"]}d window')}")
    print()

    if not data["buckets"]:
        print(f"  {_c(_YELLOW, 'no calibration data yet')}")
        print(f"  {_c(_DIM, 'need usage ticks that overlap with JSONL session data.')}")
        print(f"  {_c(_DIM, 'keep ccmeter poll running while you use Claude Code.')}")
        return

    for bucket, bdata in data["buckets"].items():
        print(f"  {_hr()}")
        print(f"  {_c(_BOLD, bucket)} {_c(_DIM, _pl(bdata['ticks'], 'tick'))}")
        print(f"  {_hr()}")
        if bdata["mixed_ticks"]:
            print(f"  {_c(_YELLOW, f'⚠ {_pl(bdata["mixed_ticks"], "tick")} mixed models')}")
        for model, mdata in sorted(bdata["models"].items()):
            tpp = mdata["avg_per_pct"]
            act = bdata.get("activity_per_pct", {})
            print(f"  {_c(_CYAN, model)}")
            print()
            print(f"    {_c(_DIM, '1%  ≈')}  {_c(_BOLD + _WHITE, f'{mdata["avg_total_per_pct"]:,}')} {_c(_DIM, 'tokens')}")
            print(
                f"           "
                f"{_c(_PURPLE, f'{tpp["input"]:,}')} {_c(_DIM, 'in')}  "
                f"{_c(_PURPLE, f'{tpp["output"]:,}')} {_c(_DIM, 'out')}  "
                f"{_c(_PURPLE, f'{tpp["cache_read"]:,}')} {_c(_DIM, 'cache_r')}  "
                f"{_c(_PURPLE, f'{tpp["cache_create"]:,}')} {_c(_DIM, 'cache_w')}"
            )
            if act and (act.get("tool_calls") or act.get("lines_added")):
                parts = []
                if act.get("tool_calls"):
                    parts.append(f"{_c(_WHITE, f'{act["tool_calls"]:.0f}')} {_c(_DIM, 'tool calls')}")
                if act.get("reads"):
                    parts.append(f"{_c(_WHITE, f'{act["reads"]:.0f}')} {_c(_DIM, 'reads')}")
                if act.get("writes"):
                    parts.append(f"{_c(_WHITE, f'{act["writes"]:.0f}')} {_c(_DIM, 'edits')}")
                if act.get("bash"):
                    parts.append(f"{_c(_WHITE, f'{act["bash"]:.0f}')} {_c(_DIM, 'bash')}")
                print(f"           {'  ·  '.join(parts)}")
                added = act.get("lines_added", 0)
                removed = act.get("lines_removed", 0)
                if added or removed:
                    print(f"           {_c(_GREEN, f'+{added:.0f}')} / {_c(_RED, f'-{removed:.0f}')} {_c(_DIM, 'lines')}")
            print()

    print(f"  {_c(_DIM, '⚠  claude.ai + claude code simultaneously = inflated counts')}")
    print(f"  {_c(_DIM, '   api tracks combined usage; we only see local logs')}")
