"""Generate calibration report by cross-referencing usage ticks against JSONL token data."""

from __future__ import annotations

import bisect
import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from ccmeter import __version__
from ccmeter.activity import ActivityEvent, activity_in_window_by_model
from ccmeter.auth import fetch_account_id, get_credentials
from ccmeter.db import connect
from ccmeter.display import BOLD, CYAN, DIM, GREEN, PINK, PURPLE, RED, WHITE, YELLOW, c, hr, human, pl
from ccmeter.scan import scan

# API pricing per MTok (USD).
# Source: platform.claude.com/docs/en/about-claude/pricing
# cache_create uses the 5-minute TTL price. 1h TTL is ~1.6x more expensive
# but JSONL doesn't distinguish which TTL was used. ~8% underestimate if all 1h.
PRICING = {
    "claude-opus-4-6": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_create": 6.25},
    "claude-opus-4-5": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_create": 6.25},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_create": 3.75},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_create": 3.75},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_create": 1.25},
}

FALLBACK_PRICING = PRICING["claude-opus-4-6"]
APPROX = "\u2248"  # ≈

# Bucket display names and window descriptions
BUCKET_LABELS: dict[str, str] = {
    "five_hour": "5h window",
    "seven_day": "7d window",
    "seven_day_sonnet": "7d sonnet",
    "seven_day_opus": "7d opus",
    "seven_day_cowork": "7d cowork",
    "seven_day_oauth_apps": "7d oauth apps",
    "extra_usage": "extra usage",
}

# Early warning thresholds from Claude Code source.
# Format: (utilization_threshold, max_time_elapsed_fraction)
# "Warn when utilization >= X and time elapsed <= Y of window"
EARLY_WARNINGS: dict[str, list[tuple[float, float]]] = {
    "five_hour": [(90.0, 0.72)],
    "seven_day": [(25.0, 0.15), (50.0, 0.35), (75.0, 0.60)],
}


def account_clause(account_id: str | None) -> Callable[..., str]:
    """Return a function that generates SQL account_id filter clauses.

    If account_id is set, filters to that account. Otherwise matches all rows.
    Usage: af = account_clause(id); af("s1") → "s1.account_id = 'uuid'"
    """
    if not account_id:

        def _all(prefix: str = "") -> str:
            return "1=1"

        return _all
    # UUID format — safe to inline (validated by API response structure)
    safe = account_id.replace("'", "")

    def _filter(prefix: str = "") -> str:
        return f"{prefix}.account_id = '{safe}'" if prefix else f"account_id = '{safe}'"

    return _filter


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


def burn_rate(utilization: float, resets_at: str, window_hours: float) -> dict[str, Any] | None:
    """Compute burn rate and predict exhaustion from current utilization and reset time.

    The rolling window means resets_at is when the *oldest* usage in the current window
    expires — not a fixed endpoint. remaining_secs / window_secs gives the fraction of
    the window still ahead, and (1 - that) is the fraction already consumed by time.
    This is the best approximation we have without knowing the exact window start.
    """
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    reset = datetime.fromisoformat(resets_at)
    if reset.tzinfo is None:
        reset = reset.replace(tzinfo=timezone.utc)

    remaining_secs = (reset - now).total_seconds()
    if remaining_secs <= 0:
        return None

    window_secs = window_hours * 3600
    # Clamp: remaining can't exceed window (would mean elapsed < 0)
    remaining_secs = min(remaining_secs, window_secs)
    elapsed_secs = window_secs - remaining_secs
    if elapsed_secs < 60:
        return None

    elapsed_frac = elapsed_secs / window_secs
    rate = utilization / (elapsed_secs / 3600)
    remaining_pct = 100.0 - utilization
    mins_to_exhaust = (remaining_pct / rate * 60) if rate > 0 else float("inf")

    # Check against early warning thresholds (highest severity first)
    bucket_key = "five_hour" if window_hours <= 6 else "seven_day"
    warning = None
    for threshold_util, max_elapsed in EARLY_WARNINGS.get(bucket_key, []):
        if utilization >= threshold_util and elapsed_frac <= max_elapsed:
            warning = "critical"
            break
        # Predictive: on pace to cross threshold before the danger-zone time?
        if rate > 0 and utilization < threshold_util:
            hours_to_threshold = (threshold_util - utilization) / rate
            frac_at_threshold = (elapsed_secs + hours_to_threshold * 3600) / window_secs
            if frac_at_threshold <= max_elapsed:
                warning = "warning"

    return {
        "rate_pct_per_hour": rate,
        "elapsed_frac": elapsed_frac,
        "remaining_mins": mins_to_exhaust,
        "warning": warning,
    }


def model_filter_for(bucket: str) -> str | None:
    """Extract model filter from bucket name. e.g. 'seven_day_sonnet' → 'claude-sonnet'."""
    model_buckets = {
        "seven_day_sonnet": "claude-sonnet",
        "seven_day_opus": "claude-opus",
    }
    return model_buckets.get(bucket)


def tokens_in_window(events: list[Any], t0: str, t1: str, model_prefix: str | None = None) -> dict[str, dict[str, int]]:
    """Sum token counts per model for events between two timestamps."""
    lo = bisect.bisect_left(events, t0, key=lambda e: e.ts)
    hi = bisect.bisect_right(events, t1, key=lambda e: e.ts)
    by_model: dict[str, dict[str, int]] = defaultdict(
        lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0, "count": 0}
    )
    for i in range(lo, hi):
        e = events[i]
        m = e.model or "unknown"
        if model_prefix and not m.startswith(model_prefix):
            continue
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
    model_prefix = model_filter_for(bucket)
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
        by_model = tokens_in_window(events, t0, t1, model_prefix)
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

        activity_by_model: dict[str, Any] = {}
        if activity_events:
            activity_by_model = activity_in_window_by_model(activity_events, t0, t1)

        calibrations.append(
            {
                "t0": t0,
                "t1": t1,
                "delta_pct": delta,
                "models": models,
                "cost_per_pct": tick_cost,  # combined across models
                "mixed": len(models) > 1,
                "activity_by_model": activity_by_model,
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
        activity_keys = ("tool_calls", "reads", "writes", "bash", "lines_added", "lines_removed")
        model_agg: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"weights": [], "cost_per_pct": [], "cache_ratio": [], "ticks": 0}
        )
        for cal in cals:
            w = 1.0 / cal["delta_pct"]
            delta = cal["delta_pct"]
            for model, data in cal["models"].items():
                model_agg[model]["ticks"] += 1
                model_agg[model]["weights"].append(w)
                model_agg[model]["cost_per_pct"].append(data["cost_per_pct"])
                model_agg[model]["cache_ratio"].append(data["cache_ratio"])
                for k in ("input", "output", "cache_read", "cache_create"):
                    model_agg[model].setdefault(f"{k}_per_pct", []).append(data["tokens_per_pct"][k])
                # Per-model activity from this tick
                model_act = cal.get("activity_by_model", {}).get(model)
                if model_act:
                    for k in activity_keys:
                        model_agg[model].setdefault(f"act_{k}", []).append((model_act[k] / delta, w))

        model_summary = {}
        for model, agg in model_agg.items():
            mw = agg["weights"]
            tw = sum(mw)
            act_summary: dict[str, float] = {}
            for k in activity_keys:
                pairs = agg.get(f"act_{k}", [])
                if pairs:
                    act_tw = sum(w for _, w in pairs)
                    act_summary[k] = round(sum(v * w for v, w in pairs) / act_tw, 1)
            model_summary[model] = {
                "ticks": agg["ticks"],
                "avg_cost_per_pct": sum(v * w for v, w in zip(agg["cost_per_pct"], mw, strict=True)) / tw,
                "avg_cache_ratio": sum(v * w for v, w in zip(agg["cache_ratio"], mw, strict=True)) / tw,
                "avg_per_pct": {
                    k: int(sum(v * w for v, w in zip(agg[f"{k}_per_pct"], mw, strict=True)) / tw)
                    for k in ("input", "output", "cache_read", "cache_create")
                },
                "activity_per_pct": act_summary,
            }

        total_weight = sum(weights)
        avg_cost = sum(cost * w for cost, w in zip(costs, weights, strict=True)) / total_weight
        capacity = avg_cost * 100
        base_budget = capacity / multiplier if multiplier > 1 else capacity

        # Hours spanned: first sample in this bucket to now
        from datetime import datetime, timezone

        row = conn.execute(
            "SELECT MIN(ts) FROM usage_samples WHERE bucket = ?",
            (bucket,),
        ).fetchone()
        first_sample = datetime.fromisoformat(row[0])
        if first_sample.tzinfo is None:
            first_sample = first_sample.replace(tzinfo=timezone.utc)
        span_hours = (datetime.now(tz=timezone.utc) - first_sample).total_seconds() / 3600

        report_data["buckets"][bucket] = {
            "ticks": len(cals),
            "span_hours": span_hours,
            "mixed_ticks": sum(1 for cc in cals if cc["mixed"]),
            "avg_cost_per_pct": avg_cost,
            "capacity": capacity,
            "base_budget": base_budget,
            "models": model_summary,
        }

    # Live state: current utilization + burn rate per bucket
    window_hours: dict[str, float] = {
        "five_hour": 5.0,
        "seven_day": 168.0,
        "seven_day_opus": 168.0,
        "seven_day_sonnet": 168.0,
        "seven_day_cowork": 168.0,
        "seven_day_oauth_apps": 168.0,
    }
    live_rows = conn.execute(
        f"""SELECT bucket, utilization, resets_at, ts FROM usage_samples
           WHERE {af()} AND id IN (SELECT MAX(id) FROM usage_samples WHERE {af()} GROUP BY bucket)"""
    ).fetchall()
    live: dict[str, dict[str, Any]] = {}
    for r in live_rows:
        bucket = r["bucket"]
        util = r["utilization"]
        resets_at = r["resets_at"]
        wh = window_hours.get(bucket)
        entry: dict[str, Any] = {"utilization": util, "ts": r["ts"]}
        if resets_at and wh and util > 0:
            br = burn_rate(util, resets_at, wh)
            if br:
                entry["burn"] = br
        # Combine with calibrated budget if available
        bdata = report_data["buckets"].get(bucket)
        if bdata and entry.get("burn"):
            cost_per_pct = bdata["avg_cost_per_pct"]
            rate_pct_h = entry["burn"]["rate_pct_per_hour"]
            entry["dollars_per_hour"] = cost_per_pct * rate_pct_h
            entry["dollars_remaining"] = cost_per_pct * (100.0 - util)
        live[bucket] = entry
    report_data["live"] = live

    conn.close()

    if json_output:
        print(json.dumps(report_data, indent=2))
        return

    _print_report(report_data)


def _print_report(data: dict[str, Any]) -> None:
    # Spacing rules (never double \n):
    #   \n above divider. \n below divider. \n between models.
    #   \n above disclaimer. trailing \n.
    multiplier = data.get("multiplier", 1)
    tier = tier_label(data.get("rate_limit_tier", ""), multiplier)
    approx = APPROX

    ver = data.get("version", "?")
    sessions = data["sessions"]
    events = data["token_events"]
    samples = data["usage_samples"]
    lookback = data["lookback_days"]
    print(f"  {c(BOLD + WHITE, 'ccmeter')} {c(DIM, 'v' + ver)}    {c(PINK, tier)}")
    print(f"  {c(DIM, f'{sessions:,} sessions  ·  {events:,} events  ·  {samples} samples  ·  {lookback}d')}")

    if not data["buckets"]:
        print()
        print(f"  {c(YELLOW, 'no calibration data yet')}")
        print(f"  {c(DIM, 'need usage ticks that overlap with JSONL session data.')}")
        print(f"  {c(DIM, 'keep ccmeter poll running while you use Claude Code.')}")
        return

    model_order = {"opus": 0, "sonnet": 1, "haiku": 2}

    def model_sort_key(item: tuple[str, Any]) -> int:
        name = item[0]
        for prefix, rank in model_order.items():
            if prefix in name:
                return rank
        return 99

    dot = f" {c(DIM, '·')} "

    for bucket, bdata in data["buckets"].items():
        window = BUCKET_LABELS.get(bucket) or bucket
        capacity = bdata["capacity"]
        base = bdata["base_budget"]
        hours = bdata.get("span_hours", 0)
        if hours >= 24:
            days = int(hours // 24)
            rem_h = int(hours % 24)
            span_str = f" over {days}d {rem_h}h" if rem_h else f" over {days}d"
        elif hours >= 1:
            span_str = f" over {int(hours)}h"
        else:
            span_str = ""
        ticks_label = pl(bdata["ticks"], "tick")

        print()  # \n above divider
        print(f"  {hr()}")
        print()  # \n below divider
        confidence = ""
        if bdata["ticks"] < 3:
            confidence = f"  {c(YELLOW, 'low confidence')}"
        print(f"  {c(BOLD + WHITE, window)}  {c(DIM, f'{ticks_label}{span_str}')}{confidence}")
        if multiplier > 1:
            print(
                f"  {c(BOLD + WHITE, f'${capacity:.0f}')} {c(DIM, approx)} {c(DIM, f'{multiplier}x')} {c(WHITE, f'${base:.0f}')} {c(DIM, 'pro base')}"
            )
        else:
            print(f"  {c(BOLD + WHITE, f'${capacity:.0f}')} {c(DIM, 'budget')}")

        for model, mdata in sorted(bdata["models"].items(), key=model_sort_key):
            tpp = mdata["avg_per_pct"]
            cache_pct = int(mdata["avg_cache_ratio"] * 100)
            cost = mdata["avg_cost_per_pct"]
            act = mdata.get("activity_per_pct", {})

            short_model = next((t for t in ("opus", "sonnet", "haiku") if t in model), model.replace("claude-", ""))
            cache_str = f" {c(DIM, '·')} {c(WHITE, f'{cache_pct}%')} {c(DIM, 'cached')}" if cache_pct > 0 else ""
            token_parts = [
                f"{c(PURPLE, human(tpp['input']))} {c(DIM, 'in')}",
                f"{c(PURPLE, human(tpp['output']))} {c(DIM, 'out')}",
                f"{c(PURPLE, human(tpp['cache_read']))} {c(DIM, 'cr')}",
                f"{c(PURPLE, human(tpp['cache_create']))} {c(DIM, 'cw')}",
            ]
            print()  # \n between models
            print(
                f"  {c(CYAN, f'{short_model:<8}')}{c(WHITE, f'${cost:.2f}')}{cache_str} {c(DIM, '|')} {dot.join(token_parts)}"
            )

            act_parts = []
            for key, label in [("tool_calls", "tools"), ("reads", "reads"), ("writes", "edits"), ("bash", "bash")]:
                v = act.get(key)
                if v:
                    act_parts.append(f"{c(WHITE, f'{v:.0f}')} {c(DIM, label)}")
            added = act.get("lines_added", 0)
            removed = act.get("lines_removed", 0)
            if added or removed:
                act_parts.append(f"{c(GREEN, f'+{added:.0f}')}/{c(RED, f'-{removed:.0f}')} {c(DIM, 'loc')}")
            if act_parts:
                print(f"          {dot.join(act_parts)}")

    # Binding constraint analysis — includes model-specific buckets
    five_h = data["buckets"].get("five_hour")
    seven_d = data["buckets"].get("seven_day")
    model_buckets = {k: v for k, v in data["buckets"].items() if k in ("seven_day_opus", "seven_day_sonnet")}

    if five_h and seven_d:
        print()  # \n above divider
        print(f"  {hr()}")
        print()  # \n below divider

        windows_per_week = 7 * 24 / 5  # 33.6
        max_from_5h = five_h["capacity"] * windows_per_week
        cap_7d = seven_d["capacity"]
        ratio = seven_d["capacity"] / max_from_5h * 100
        print(f"  {c(DIM, 'if you maxed every 5h window:')} {c(WHITE, f'${max_from_5h:,.0f}')}{c(DIM, '/7d')}")
        print(f"  {c(DIM, '7d cap:')} {c(WHITE, f'${cap_7d:,.0f}')}")
        print(f"  {c(DIM, '7d limits you to')} {c(YELLOW, f'{ratio:.0f}%')} {c(DIM, 'of theoretical 5h throughput')}")

        # Model-specific constraints: separate opus/sonnet budgets can bind independently
        for mb_name, mb_data in model_buckets.items():
            short = mb_name.replace("seven_day_", "")
            mb_cap = mb_data["capacity"]
            # What fraction of 7d aggregate does this model-specific cap represent?
            model_ratio = mb_cap / cap_7d * 100 if cap_7d > 0 else 0
            print(
                f"  {c(DIM, f'{short} 7d cap:')} {c(WHITE, f'${mb_cap:,.0f}')} {c(DIM, f'({model_ratio:.0f}% of aggregate)')}"
            )

    # Live burn rate — connects calibrated budget to current utilization
    live = data.get("live", {})
    live_buckets = {k: v for k, v in live.items() if v.get("burn") and k in data["buckets"]}
    if live_buckets:
        print()  # \n above divider
        print(f"  {hr()}")
        print()  # \n below divider
        print(f"  {c(BOLD + WHITE, 'now')}")

        # Find which bucket exhausts first
        first_bucket = None
        first_mins = float("inf")
        for bname, ldata in live_buckets.items():
            mins = ldata["burn"]["remaining_mins"]
            if mins < first_mins:
                first_mins = mins
                first_bucket = bname

        for bname, ldata in live_buckets.items():
            window = BUCKET_LABELS.get(bname) or bname
            util = ldata["utilization"]
            br = ldata["burn"]
            dph = ldata.get("dollars_per_hour", 0)
            remaining_usd = ldata.get("dollars_remaining", 0)
            mins = br["remaining_mins"]
            warning = br["warning"]

            # Format time remaining
            if mins < 60:
                time_str = f"~{mins:.0f}m"
            elif mins < 1440:
                time_str = f"~{mins / 60:.0f}h"
            else:
                time_str = f"~{mins / 1440:.0f}d"

            # Rate in appropriate unit
            rate = br["rate_pct_per_hour"]
            is_weekly = bname != "five_hour"
            if is_weekly:
                rate_str = f"{rate * 24:.0f}%/d"
                dpr = dph * 24
                dollar_rate = f"${dpr:.0f}/d" if dpr >= 1 else f"${dph:.2f}/h"
            else:
                rate_str = f"{rate:.0f}%/h"
                dollar_rate = f"${dph:.2f}/h"

            warn_color = RED if warning == "critical" else YELLOW if warning == "warning" else DIM
            binding = f"  {c(RED, '← binding')}" if bname == first_bucket and len(live_buckets) > 1 else ""

            print(
                f"  {c(DIM, f'{window:<14}')}"
                f" {c(warn_color, f'{util:.0f}%')}"
                f"  {c(DIM, rate_str)}"
                f"  {c(WHITE, dollar_rate)}"
                f"  {c(DIM, f'${remaining_usd:.0f} left')}"
                f"  {c(PINK if mins < 120 else DIM, time_str)}"
                f"{binding}"
            )

    print()  # \n above disclaimer
    print(f"  {c(DIM, '⚠  claude.ai usage during a tick makes budget estimates conservative')}")
    print()  # trailing \n
