"""Generate calibration report by cross-referencing usage ticks against JSONL token data."""

from __future__ import annotations

import bisect
import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from ccmeter import __version__
from ccmeter.activity import ActivityEvent, activity_in_window
from ccmeter.auth import fetch_account_id, get_credentials
from ccmeter.db import connect
from ccmeter.display import BOLD, CYAN, DIM, GREEN, PINK, PURPLE, RED, WHITE, YELLOW, c, hr, human, pl
from ccmeter.scan import scan

# API pricing per MTok (USD).
# Source: anthropic.com/pricing — Opus 4.6/Sonnet 4.6/Haiku 4.5
PRICING = {
    "claude-opus-4-6": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_create": 6.25},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_create": 3.75},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_create": 1.25},
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


def account_clause(account_id: str | None) -> Callable[..., str]:
    """Return a function that generates SQL account_id filter clauses.

    If account_id is set, filters to that account. Otherwise matches all rows.
    Usage: af = account_clause(id); af("s1") → "s1.account_id = 'uuid'"
    """
    if not account_id:
        return lambda prefix="": "1=1"
    # UUID format — safe to inline (validated by API response structure)
    safe = account_id.replace("'", "")
    return lambda prefix="", _id=safe: f"{prefix}.account_id = '{_id}'" if prefix else f"account_id = '{_id}'"


def pricing_for(model: str) -> dict[str, float]:
    for prefix, rates in PRICING.items():
        if model.startswith(prefix):
            return rates
    return FALLBACK_PRICING


def cost_usd(tokens: dict[str, int], model: str) -> float:
    """Compute cost in USD for a token breakdown."""
    rates = pricing_for(model)
    return sum(
        tokens.get(k, 0) * rates.get(k, 0) / 1_000_000 for k in ("input", "output", "cache_read", "cache_create")
    )


def parse_multiplier(rate_limit_tier: str) -> int:
    """Extract tier multiplier from rate_limit_tier string. e.g. 'default_claude_max_20x' → 20."""
    if "_max_" in rate_limit_tier and rate_limit_tier.endswith("x"):
        try:
            return int(rate_limit_tier.rsplit("_", maxsplit=1)[-1].rstrip("x"))
        except ValueError:
            pass
    return 1


def tier_label(rate_limit_tier: str, multiplier: int) -> str:
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
    account_id: str | None = None,
) -> list[dict[str, Any]]:
    """Find utilization ticks and calculate cost per percent across all models."""
    af = account_clause(account_id)
    rows = conn.execute(
        f"""
        SELECT s1.ts as t0, s2.ts as t1,
               s1.utilization as u0, s2.utilization as u1,
               s2.utilization - s1.utilization as delta_pct
        FROM usage_samples s1
        JOIN usage_samples s2
            ON s2.bucket = s1.bucket
            AND {af("s2")}
            AND s2.id = (SELECT MIN(id) FROM usage_samples
                         WHERE bucket = s1.bucket AND {af()} AND id > s1.id)
        WHERE s1.bucket = ?
            AND {af("s1")}
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
            cost = cost_usd(tpp, model)
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
    account_id = None
    if creds:
        tier = creds.subscription_type or "unknown"
        rate_tier = creds.rate_limit_tier or "unknown"
        account_id = fetch_account_id(creds.access_token)

    multiplier = parse_multiplier(rate_tier)
    af = account_clause(account_id)

    result = scan(days=days, recache=recache)

    if not result.events:
        print(f"no token events found in the last {days} days.")
        print("make sure Claude Code has been used and JSONL logs exist in ~/.claude/projects/")
        return

    conn = connect()
    sample_count = conn.execute(f"SELECT COUNT(*) as n FROM usage_samples WHERE {af()}").fetchone()["n"]

    if sample_count == 0:
        print("no usage samples collected yet. run: ccmeter poll")
        conn.close()
        return

    buckets_row = conn.execute(f"SELECT DISTINCT bucket FROM usage_samples WHERE {af()}").fetchall()
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
        cals = calibrate_bucket(bucket, result.events, conn, activity_events=result.activity, account_id=account_id)
        if not cals:
            continue

        # Aggregate cost per percent across all ticks (combined across models)
        # Weight by 1/delta — a 1-tick observation is higher confidence than a 4-tick gap
        costs = [cal["cost_per_pct"] for cal in cals]
        weights = [1.0 / cal["delta_pct"] for cal in cals]

        # Per-model detail (weighted by 1/delta for consistency with headline)
        model_agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"weights": [], "cost_per_pct": [], "cache_ratio": [], "ticks": 0}
        )
        activity_agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"weights": [], "values": []})
        for cal in cals:
            w = 1.0 / cal["delta_pct"]
            for model, data in cal["models"].items():
                model_agg[model]["ticks"] += 1
                model_agg[model]["weights"].append(w)
                model_agg[model]["cost_per_pct"].append(data["cost_per_pct"])
                model_agg[model]["cache_ratio"].append(data["cache_ratio"])
                for k in ("input", "output", "cache_read", "cache_create"):
                    model_agg[model].setdefault(f"{k}_per_pct", []).append(data["tokens_per_pct"][k])
            if cal.get("activity"):
                act = cal["activity"]
                delta = cal["delta_pct"]
                for k in ("prompts", "turns", "tool_calls", "reads", "writes", "bash", "lines_added", "lines_removed"):
                    activity_agg[k]["weights"].append(w)
                    activity_agg[k]["values"].append(act[k] / delta)

        model_summary = {}
        for model, agg in model_agg.items():
            mw = agg["weights"]
            tw = sum(mw)
            model_summary[model] = {
                "ticks": agg["ticks"],
                "avg_cost_per_pct": sum(v * w for v, w in zip(agg["cost_per_pct"], mw)) / tw,
                "avg_cache_ratio": sum(v * w for v, w in zip(agg["cache_ratio"], mw)) / tw,
                "avg_per_pct": {
                    k: int(sum(v * w for v, w in zip(agg[f"{k}_per_pct"], mw)) / tw)
                    for k in ("input", "output", "cache_read", "cache_create")
                },
            }

        activity_summary = {}
        for k, agg in activity_agg.items():
            if agg["weights"]:
                tw = sum(agg["weights"])
                activity_summary[k] = round(sum(v * w for v, w in zip(agg["values"], agg["weights"])) / tw, 1)

        total_weight = sum(weights)
        avg_cost = sum(cost * w for cost, w in zip(costs, weights)) / total_weight
        capacity = avg_cost * 100
        base_budget = capacity / multiplier if multiplier > 1 else capacity

        report_data["buckets"][bucket] = {
            "ticks": len(cals),
            "mixed_ticks": sum(1 for cc in cals if cc["mixed"]),
            "avg_cost_per_pct": avg_cost,
            "capacity": capacity,
            "base_budget": base_budget,
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
    tier = tier_label(data.get("rate_limit_tier", ""), multiplier)

    print()
    ver = data.get("version", "?")
    sessions = data["sessions"]
    events = data["token_events"]
    samples = data["usage_samples"]
    lookback = data["lookback_days"]
    print(f"  {c(BOLD + WHITE, 'ccmeter')} {c(DIM, 'v' + ver)}    {c(PINK, tier)}")
    print(f"  {c(DIM, f'{sessions:,} sessions  ·  {events:,} events  ·  {samples} samples  ·  {lookback}d')}")
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

        print()

        if bdata["mixed_ticks"]:
            mixed = pl(bdata["mixed_ticks"], "tick")
            print(f"  {c(DIM, f'{mixed} mixed models — cost stayed consistent? validates metering model')}")
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
            for key, label in [("tool_calls", "tools"), ("reads", "reads"), ("writes", "edits"), ("bash", "bash")]:
                v = act.get(key)
                if v:
                    aparts.append(f"{c(WHITE, f'{v:.0f}')} {c(DIM, label)}")
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
        cap_7d = seven_d["capacity"]
        print(f"  {c(DIM, '7d cap:')} {c(WHITE, f'${cap_7d:,.0f}')}")
        ratio = seven_d["capacity"] / max_from_5h * 100
        print(f"  {c(DIM, '7d limits you to')} {c(YELLOW, f'{ratio:.0f}%')} {c(DIM, 'of theoretical 5h throughput')}")
        print()

    print(f"  {c(DIM, '⚠  claude.ai usage during a tick makes budget estimates conservative')}")
