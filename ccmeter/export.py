"""Export anonymized calibration data for community sharing."""

import json
from typing import Any

from ccmeter import __version__
from ccmeter.auth import get_credentials
from ccmeter.db import connect
from ccmeter.report import calibrate_bucket
from ccmeter.scan import scan


def run_export(days: int = 30):
    """Dump a standardized JSON blob stripped of identifying info."""
    creds = get_credentials()
    tier = "unknown"
    rate_tier = "unknown"
    if creds:
        tier = creds.subscription_type or "unknown"
        rate_tier = creds.rate_limit_tier or "unknown"

    result = scan(days=days)
    if not result.events:
        print("no token events to export")
        return

    conn = connect()
    sample_count = conn.execute("SELECT COUNT(*) as n FROM usage_samples").fetchone()["n"]
    if sample_count == 0:
        print("no usage samples to export. run: ccmeter poll")
        conn.close()
        return

    buckets = ["five_hour", "seven_day", "seven_day_sonnet"]
    export: dict[str, Any] = {
        "ccmeter_version": __version__,
        "tier": tier,
        "rate_limit_tier": rate_tier,
        "os": result.os,
        "period_days": days,
        "buckets": {},
    }

    for bucket in buckets:
        cals = calibrate_bucket(bucket, result.events, conn)
        if not cals:
            continue

        for cal in cals:
            for model, mdata in cal["models"].items():
                key = f"{bucket}/{model}"
                if key not in export["buckets"]:
                    export["buckets"][key] = {"ticks": 0, "samples": []}
                export["buckets"][key]["ticks"] += 1
                export["buckets"][key]["samples"].append(
                    {
                        "delta_pct": cal["delta_pct"],
                        "tokens_per_pct": mdata["tokens_per_pct"],
                        "total_per_pct": mdata["total_per_pct"],
                        "messages": mdata["message_count"],
                    }
                )

    conn.close()

    print(json.dumps(export, indent=2))
