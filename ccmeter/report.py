"""Generate calibration report by cross-referencing usage ticks against JSONL token data."""

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
    return sum(
        tokens.get(k, 0) * rates.get(k, 0) / 1_000_000 for k in ("input", "output", "cache_read", "cache_create")
    )


def tokens_in_window(events: list[Any], t0: str, t1: str) -> dict[str, dict[str, int]]:
    """Sum token counts per model for events between two timestamps."""
    by_model: dict[str, dict[str, int]] = defaultdict(
        lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0, "count": 0}
    )
    for e in events:
        if t0 <= e.ts <= t1:
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

        model_agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"ticks": 0, "total_per_pct": [], "cost_per_pct": [], "cache_ratio": []}
        )
        activity_agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"ticks": 0, "values": []})
        for cal in cals:
            for model, data in cal["models"].items():
                model_agg[model]["ticks"] += 1
                model_agg[model]["total_per_pct"].append(data["total_per_pct"])
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
                "avg_total_per_pct": int(sum(agg["total_per_pct"]) / n),
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

        mixed_count = sum(1 for cc in cals if cc["mixed"])
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


def _print_report(data: dict[str, Any]) -> None:
    print()
    print(
        f"  {c(BOLD + WHITE, 'ccmeter')} {c(DIM, f'v{data.get("version", "?")}')}    {c(PINK, data['tier'])} {c(DIM, data['rate_limit_tier'])}"
    )
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
        print(f"  {hr()}")
        label = bucket.replace("_", " ")
        print(f"  {c(BOLD + WHITE, label)}  {c(DIM, pl(bdata['ticks'], 'tick'))}")
        if bdata["mixed_ticks"]:
            print(f"  {c(YELLOW, f'⚠ {pl(bdata["mixed_ticks"], "tick")} had mixed models — estimates less reliable')}")
        print()

        for model, mdata in sorted(bdata["models"].items()):
            tpp = mdata["avg_per_pct"]
            act = bdata.get("activity_per_pct", {})
            cache_pct = int(mdata["avg_cache_ratio"] * 100)
            cost = mdata["avg_cost_per_pct"]

            # model name
            print(f"  {c(CYAN, model)}")

            # headline: cost per 1%
            cost_100 = cost * 100
            print(f"    {c(DIM, '1%  ≈')}  {c(BOLD + WHITE, f'${cost:.3f}')} {c(DIM, 'API-equivalent')}")
            print(f"    {c(DIM, '100% ≈')}  {c(DIM, f'${cost_100:.2f}')}")

            # token breakdown
            parts = [
                f"{c(PURPLE, human(tpp['input']))} {c(DIM, 'in')}",
                f"{c(PURPLE, human(tpp['output']))} {c(DIM, 'out')}",
                f"{c(PURPLE, human(tpp['cache_read']))} {c(DIM, 'cache↓')}",
                f"{c(PURPLE, human(tpp['cache_create']))} {c(DIM, 'cache↑')}",
            ]
            print(f"           {'  '.join(parts)}")

            # cache ratio
            if cache_pct > 0:
                print(
                    f"           {c(DIM, f'{cache_pct}% cached')}  {c(DIM, f'({human(mdata["avg_total_per_pct"])} raw tokens)')}"
                )

            # activity
            if act and (act.get("tool_calls") or act.get("lines_added")):
                aparts = []
                if act.get("tool_calls"):
                    aparts.append(f"{c(WHITE, f'{act["tool_calls"]:.0f}')} {c(DIM, 'tools')}")
                if act.get("reads"):
                    aparts.append(f"{c(WHITE, f'{act["reads"]:.0f}')} {c(DIM, 'reads')}")
                if act.get("writes"):
                    aparts.append(f"{c(WHITE, f'{act["writes"]:.0f}')} {c(DIM, 'edits')}")
                if act.get("bash"):
                    aparts.append(f"{c(WHITE, f'{act["bash"]:.0f}')} {c(DIM, 'bash')}")
                print(f"           {'  ·  '.join(aparts)}")
                added = act.get("lines_added", 0)
                removed = act.get("lines_removed", 0)
                if added or removed:
                    print(f"           {c(GREEN, f'+{added:.0f}')} / {c(RED, f'-{removed:.0f}')} {c(DIM, 'lines')}")
            print()

    print(f"  {c(DIM, '⚠  claude.ai + claude code simultaneously = inflated counts')}")
    print(f"  {c(DIM, '   api tracks combined usage; we only see local logs')}")
