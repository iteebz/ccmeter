"""Generate calibration report by cross-referencing usage ticks against JSONL token data."""

import bisect
import json
import sqlite3
from collections import defaultdict
from typing import Any

from ccmeter import __version__
from ccmeter.activity import ActivityEvent, activity_in_window
from ccmeter.auth import get_credentials
from ccmeter.db import connect
from ccmeter.display import BOLD, CYAN, DIM, GREEN, PINK, PURPLE, RED, WHITE, YELLOW, c, hr, human, pl
from ccmeter.scan import scan

# API pricing per MTok (USD).
# Source: anthropic.com/pricing as of 2026-03.
PRICING = {
    "claude-opus-4-6": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_create": 6.25},
    "claude-sonnet-4-6": {"input": 1.50, "output": 7.50, "cache_read": 0.15, "cache_create": 1.875},
    "claude-haiku-4-5": {"input": 0.40, "output": 2.00, "cache_read": 0.04, "cache_create": 0.50},
}

FALLBACK_PRICING = PRICING["claude-opus-4-6"]

# Bucket display names and window descriptions
BUCKET_LABELS: dict[str, str] = {
    "five_hour": "5h window",
    "seven_day": "7d window",
    "seven_day_sonnet": "7d sonnet",
    "seven_day_opus": "7d opus",
    "seven_day_cowork": "7d cowork",
    "extra_usage": "extra usage",
}


def _pricing_for(model: str) -> dict[str, float]:
    for prefix, rates in PRICING.items():
        if model.startswith(prefix):
            return rates
    return FALLBACK_PRICING


def _cost_usd(tokens: dict[str, int], model: str) -> float:
    """Compute cost in USD for a token breakdown."""
    rates = _pricing_for(model)
    return sum(
        tokens.get(k, 0) * rates.get(k, 0) / 1_000_000 for k in ("input", "output", "cache_read", "cache_create")
    )


def _parse_multiplier(rate_limit_tier: str) -> int:
    """Extract tier multiplier from rate_limit_tier string. e.g. 'default_claude_max_20x' → 20."""
    if "_max_" in rate_limit_tier and rate_limit_tier.endswith("x"):
        try:
            return int(rate_limit_tier.rsplit("_", maxsplit=1)[-1].rstrip("x"))
        except ValueError:
            pass
    return 1


def _tier_label(rate_limit_tier: str, multiplier: int) -> str:
    if multiplier > 1:
        return f"max {multiplier}x"
    if "pro" in rate_limit_tier:
        return "pro"
    return rate_limit_tier


def tokens_in_window(events: list[Any], t0: str, t1: str) -> dict[str, dict[str, int]]:
    """Sum token counts per model for events between two timestamps."""
    lo = bisect.bisect_left(events, t0, key=lambda e: e.ts)
    hi = bisect.bisect_right(events, t1, key=lambda e: e.ts)
    by_model: dict[str, dict[str, int]] = defaultdict(
        lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0, "count": 0}
    )
    for i in range(lo, hi):
        e = events[i]
        m = e.model or "unknown"
        by_model[m]["input"] += e.input_tokens
        by_model[m]["output"] += e.output_tokens
        by_model[m]["cache_read"] += e.cache_read
        by_model[m]["cache_create"] += e.cache_create
        by_model[m]["count"] += 1
    return dict(by_model)


def calibrate_bucket(
    bucket: str,
    events: list[Any],
    conn: sqlite3.Connection,
    activity_events: list[ActivityEvent] | None = None,
) -> list[dict[str, Any]]:
    """Find utilization ticks and calculate cost per percent across all models."""
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

        # Total cost across ALL models in this tick — this is the real budget drain
        tick_cost = 0.0
        models = {}
        for model, tokens in by_model.items():
            total = tokens["input"] + tokens["output"] + tokens["cache_read"] + tokens["cache_create"]
            tpp = {k: int(v / delta) for k, v in tokens.items() if k != "count"}
            cost = _cost_usd(tpp, model)
            tick_cost += cost
            cache_total = tokens["cache_read"] + tokens["cache_create"]
            models[model] = {
                "tokens": dict(tokens),
                "tokens_per_pct": tpp,
                "total_per_pct": int(total / delta),
                "cost_per_pct": cost,
                "message_count": tokens["count"],
                "cache_ratio": cache_total / total if total else 0.0,
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
                "cost_per_pct": tick_cost,  # combined across models
                "mixed": len(models) > 1,
                "activity": activity,
            }
        )
    return calibrations


def run_report(days: int = 30, json_output: bool = False, recache: bool = False):
    """Generate and display calibration report."""
    creds = get_credentials()
    tier = "unknown"
    rate_tier = "unknown"
    if creds:
        tier = creds.subscription_type or "unknown"
        rate_tier = creds.rate_limit_tier or "unknown"

    multiplier = _parse_multiplier(rate_tier)

    result = scan(days=days, recache=recache)

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

    buckets_row = conn.execute("SELECT DISTINCT bucket FROM usage_samples").fetchall()
    buckets = [r["bucket"] for r in buckets_row]
    report_data: dict[str, Any] = {
        "version": __version__,
        "tier": tier,
        "rate_limit_tier": rate_tier,
        "multiplier": multiplier,
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

        # Aggregate cost per percent across all ticks (combined across models)
        costs = [cal["cost_per_pct"] for cal in cals]

        # Per-model detail
        model_agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"ticks": 0, "cost_per_pct": [], "cache_ratio": []})
        activity_agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"ticks": 0, "values": []})
        for cal in cals:
            for model, data in cal["models"].items():
                model_agg[model]["ticks"] += 1
                model_agg[model]["cost_per_pct"].append(data["cost_per_pct"])
                model_agg[model]["cache_ratio"].append(data["cache_ratio"])
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
                "avg_cost_per_pct": sum(agg["cost_per_pct"]) / n,
                "avg_cache_ratio": sum(agg["cache_ratio"]) / n,
                "avg_per_pct": {
                    k: int(sum(agg[f"{k}_per_pct"]) / n) for k in ("input", "output", "cache_read", "cache_create")
                },
            }

        activity_summary = {}
        for k, agg in activity_agg.items():
            n = agg["ticks"]
            if n:
                activity_summary[k] = round(sum(agg["values"]) / n, 1)

        avg_cost = sum(costs) / len(costs)
        capacity = avg_cost * 100
        base_budget = capacity / multiplier if multiplier > 1 else capacity

        # Tick-based trend: compare first half vs second half
        tick_trend = None
        if len(cals) >= 4:
            mid = len(cals) // 2
            early_avg = sum(c["cost_per_pct"] for c in cals[:mid]) / mid
            late_avg = sum(c["cost_per_pct"] for c in cals[mid:]) / (len(cals) - mid)
            early_budget = early_avg * 100
            late_budget = late_avg * 100
            if early_budget > 0:
                shift = (late_budget - early_budget) / early_budget * 100
                if abs(shift) >= 0.5:
                    tick_trend = {
                        "early": early_budget,
                        "late": late_budget,
                        "shift": shift,
                        "early_ts": cals[0]["t0"],
                        "late_ts": cals[-1]["t1"],
                    }

        report_data["buckets"][bucket] = {
            "ticks": len(cals),
            "mixed_ticks": sum(1 for cc in cals if cc["mixed"]),
            "avg_cost_per_pct": avg_cost,
            "capacity": capacity,
            "base_budget": base_budget,
            "tick_trend": tick_trend,
            "tick_costs": [{"ts": cal["t0"], "cost_per_pct": cal["cost_per_pct"]} for cal in cals],
            "models": model_summary,
            "activity_per_pct": activity_summary,
        }

    conn.close()

    if json_output:
        print(json.dumps(report_data, indent=2))
        return

    _print_report(report_data)


def _print_report(data: dict[str, Any]) -> None:
    multiplier = data.get("multiplier", 1)
    tier = _tier_label(data.get("rate_limit_tier", ""), multiplier)

    print()
    print(f"  {c(BOLD + WHITE, 'ccmeter')} {c(DIM, f'v{data.get("version", "?")}')}    {c(PINK, tier)}")
    print(
        f"  {c(DIM, f'{data["sessions"]:,} sessions  ·  {data["token_events"]:,} events  ·  {data["usage_samples"]} samples  ·  {data["lookback_days"]}d')}"
    )
    print()

    if not data["buckets"]:
        print(f"  {c(YELLOW, 'no calibration data yet')}")
        print(f"  {c(DIM, 'need usage ticks that overlap with JSONL session data.')}")
        print(f"  {c(DIM, 'keep ccmeter poll running while you use Claude Code.')}")
        return

    for bucket, bdata in data["buckets"].items():
        window = BUCKET_LABELS.get(bucket) or bucket
        capacity = bdata["capacity"]
        base = bdata["base_budget"]

        print(f"  {hr()}")
        print(f"  {c(BOLD + WHITE, window)}  {c(DIM, pl(bdata['ticks'], 'tick'))}")

        # The answer: what is your budget?
        budget_line = f"  {c(BOLD + WHITE, f'${capacity:.2f}')} {c(DIM, 'budget')}"
        if multiplier > 1:
            budget_line += f"  {c(DIM, '=')} {c(DIM, f'{multiplier}x')} {c(DIM, 'x')} {c(WHITE, f'${base:.2f}')} {c(DIM, 'pro base')}"
        print(budget_line)

        # Trend: compare early ticks vs late ticks
        trend = bdata.get("tick_trend")
        if trend:
            shift = trend["shift"]
            arrow = c(GREEN, f"+{shift:.1f}%") if shift > 0 else c(RED, f"{shift:.1f}%")
            print(f"  {arrow} {c(DIM, f'early ${trend["early"]:.0f} → late ${trend["late"]:.0f}')}")
        elif bdata["ticks"] >= 4:
            print(f"  {c(DIM, 'stable across ticks')}")
        print()

        if bdata["mixed_ticks"]:
            print(
                f"  {c(DIM, f'{pl(bdata["mixed_ticks"], "tick")} mixed models — cost stayed consistent? validates metering model')}"
            )
            print()

        # Per-model breakdown
        for model, mdata in sorted(bdata["models"].items()):
            tpp = mdata["avg_per_pct"]
            act = bdata.get("activity_per_pct", {})
            cache_pct = int(mdata["avg_cache_ratio"] * 100)
            cost = mdata["avg_cost_per_pct"]

            short_model = model.replace("claude-", "")
            print(f"    {c(CYAN, short_model)}  {c(DIM, f'${cost:.3f}/1%')}", end="")
            if cache_pct > 0:
                print(f"  {c(DIM, f'{cache_pct}% cached')}", end="")
            print()

            parts = [
                f"{c(PURPLE, human(tpp['input']))} {c(DIM, 'in')}",
                f"{c(PURPLE, human(tpp['output']))} {c(DIM, 'out')}",
                f"{c(PURPLE, human(tpp['cache_read']))} {c(DIM, 'cache↓')}",
                f"{c(PURPLE, human(tpp['cache_create']))} {c(DIM, 'cache↑')}",
            ]
            print(f"      {'  '.join(parts)}")

        # Activity (not per-model — aggregate across the tick)
        act = bdata.get("activity_per_pct", {})
        if act and (act.get("tool_calls") or act.get("lines_added")):
            print()
            aparts = []
            if act.get("tool_calls"):
                aparts.append(f"{c(WHITE, f'{act["tool_calls"]:.0f}')} {c(DIM, 'tools')}")
            if act.get("reads"):
                aparts.append(f"{c(WHITE, f'{act["reads"]:.0f}')} {c(DIM, 'reads')}")
            if act.get("writes"):
                aparts.append(f"{c(WHITE, f'{act["writes"]:.0f}')} {c(DIM, 'edits')}")
            if act.get("bash"):
                aparts.append(f"{c(WHITE, f'{act["bash"]:.0f}')} {c(DIM, 'bash')}")
            print(f"    {c(DIM, 'per 1%:')}  {'  ·  '.join(aparts)}")
            added = act.get("lines_added", 0)
            removed = act.get("lines_removed", 0)
            if added or removed:
                print(f"            {c(GREEN, f'+{added:.0f}')} / {c(RED, f'-{removed:.0f}')} {c(DIM, 'lines')}")
        print()

    # Binding constraint: 5h windows vs 7d cap
    five_h = data["buckets"].get("five_hour")
    seven_d = data["buckets"].get("seven_day")
    if five_h and seven_d:
        windows_per_week = 7 * 24 / 5  # 33.6
        max_from_5h = five_h["capacity"] * windows_per_week
        print(f"  {hr()}")
        print(f"  {c(DIM, 'if you maxed every 5h window:')} {c(WHITE, f'${max_from_5h:,.0f}')}{c(DIM, '/7d')}")
        print(f"  {c(DIM, '7d cap:')} {c(WHITE, f'${seven_d["capacity"]:,.0f}')}")
        ratio = seven_d["capacity"] / max_from_5h * 100
        print(f"  {c(DIM, '7d limits you to')} {c(YELLOW, f'{ratio:.0f}%')} {c(DIM, 'of theoretical 5h throughput')}")
        print()

    print(f"  {c(DIM, '⚠  claude.ai usage during a tick makes budget estimates conservative')}")
