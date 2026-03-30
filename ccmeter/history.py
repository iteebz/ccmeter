"""Display usage sample history."""

import json

from ccmeter.db import connect


def show_history(days: int = 7, json_output: bool = False):
    conn = connect()
    rows = conn.execute(
        "SELECT ts, bucket, utilization, resets_at FROM usage_samples "
        "WHERE ts > datetime('now', ? || ' days') ORDER BY ts DESC",
        (f"-{days}",),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"no samples in the last {days} days. run: cc-meter poll")
        return

    if json_output:
        print(json.dumps([dict(r) for r in rows], indent=2))
        return

    print(f"{'timestamp':<28} {'bucket':<20} {'utilization':>6}")
    print("-" * 58)
    for r in rows:
        print(f"{r['ts']:<28} {r['bucket']:<20} {r['utilization']:>5.0f}%")

    print(f"\n{len(rows)} samples over {days} days")
