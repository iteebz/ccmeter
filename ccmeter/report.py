"""Generate calibration report by cross-referencing usage ticks against JSONL token data."""

import json
from collections import defaultdict

from ccmeter.auth import get_credentials
from ccmeter.db import connect
from ccmeter.scan import scan


def tokens_in_window(events, t0: str, t1: str) -> dict[str, dict]:
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


def calibrate_bucket(bucket: str, events, conn) -> list[dict]:
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
            models[model] = {
                "tokens": dict(tokens),
                "tokens_per_pct": {k: int(v / delta) for k, v in tokens.items() if k != "count"},
                "total_per_pct": int(total / delta),
                "message_count": tokens["count"],
            }

        calibrations.append(
            {
                "t0": t0,
                "t1": t1,
                "delta_pct": delta,
                "models": models,
                "mixed": len(models) > 1,
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

    print("scanning JSONL files...")
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
    report_data = {
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
        cals = calibrate_bucket(bucket, result.events, conn)
        if not cals:
            continue

        # aggregate per model across all ticks
        model_agg = defaultdict(lambda: {"ticks": 0, "total_per_pct": []})
        for cal in cals:
            for model, data in cal["models"].items():
                model_agg[model]["ticks"] += 1
                model_agg[model]["total_per_pct"].append(data["total_per_pct"])
                for k in ("input", "output", "cache_read", "cache_create"):
                    model_agg[model].setdefault(f"{k}_per_pct", []).append(data["tokens_per_pct"][k])

        model_summary = {}
        for model, agg in model_agg.items():
            n = agg["ticks"]
            model_summary[model] = {
                "ticks": n,
                "avg_total_per_pct": int(sum(agg["total_per_pct"]) / n),
                "avg_per_pct": {
                    k: int(sum(agg[f"{k}_per_pct"]) / n) for k in ("input", "output", "cache_read", "cache_create")
                },
            }

        mixed_count = sum(1 for c in cals if c["mixed"])
        report_data["buckets"][bucket] = {
            "ticks": len(cals),
            "mixed_ticks": mixed_count,
            "models": model_summary,
        }

    conn.close()

    if json_output:
        print(json.dumps(report_data, indent=2))
        return

    _print_report(report_data)


def _print_report(data: dict):
    print()
    print(f"tier:        {data['tier']} ({data['rate_limit_tier']})")
    print(f"os:          {data['os']}")
    print(f"cc versions: {', '.join(data['cc_versions']) or 'unknown'}")
    print(f"sessions:    {data['sessions']}")
    print(f"events:      {data['token_events']} token events over {data['lookback_days']}d")
    print(f"samples:     {data['usage_samples']} usage ticks")

    if not data["buckets"]:
        print()
        print("no calibration data yet — need usage ticks that overlap with JSONL session data.")
        print("keep ccmeter poll running while you use Claude Code.")
        return

    print()
    for bucket, bdata in data["buckets"].items():
        print(f"{bucket} ({bdata['ticks']} ticks):")
        if bdata["mixed_ticks"]:
            print(f"  ⚠ {bdata['mixed_ticks']} tick(s) had mixed models — calibration is approximate")
        for model, mdata in sorted(bdata["models"].items()):
            tpp = mdata["avg_per_pct"]
            print(f"  {model} ({mdata['ticks']} ticks):")
            print(f"    1% ≈ {mdata['avg_total_per_pct']:,} tokens")
            print(
                f"         {tpp['input']:,} input / {tpp['output']:,} output / {tpp['cache_read']:,} cache_read / {tpp['cache_create']:,} cache_create"
            )
        print()

    print("⚠  claude.ai + claude code simultaneously = inflated token counts")
    print("   (api tracks combined usage, we can only see claude code's local logs)")
