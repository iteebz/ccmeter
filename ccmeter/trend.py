"""Per-tick budget history, computed from source data."""

from ccmeter.db import connect
from ccmeter.display import BOLD, DIM, GREEN, RED, WHITE, c, hr
from ccmeter.report import BUCKET_LABELS, calibrate_bucket
from ccmeter.scan import scan


def show_trend(days: int = 30, recache: bool = False):
    result = scan(days=days, recache=recache)
    if not result.events:
        print("no token events found. run: ccmeter report")
        return

    conn = connect()
    buckets_row = conn.execute("SELECT DISTINCT bucket FROM usage_samples").fetchall()
    buckets = [r["bucket"] for r in buckets_row]

    print()
    print(f"  {c(BOLD + WHITE, 'trend')}  {c(DIM, f'{days}d')}")

    for bucket in buckets:
        cals = calibrate_bucket(bucket, result.events, conn)
        if not cals:
            continue

        window = BUCKET_LABELS.get(bucket) or bucket
        budgets = [cal["cost_per_pct"] * 100 for cal in cals]
        avg = sum(budgets) / len(budgets)

        print(f"  {hr()}")
        print(f"  {c(BOLD + WHITE, window)}  {c(DIM, f'{len(cals)} ticks')}  {c(DIM, f'avg ${avg:.0f}')}")
        print()

        for i, cal in enumerate(cals):
            ts = cal["t0"][11:16]
            date = cal["t0"][:10]
            budget = cal["cost_per_pct"] * 100
            delta_pct = cal["delta_pct"]
            msgs = sum(t["message_count"] for t in cal["models"].values())

            delta_str = ""
            if i > 0:
                prev = cals[i - 1]["cost_per_pct"] * 100
                if prev > 0:
                    shift = (budget - prev) / prev * 100
                    if abs(shift) >= 0.5:
                        delta_str = c(GREEN, f" +{shift:.0f}%") if shift > 0 else c(RED, f" {shift:.0f}%")

            print(
                f"    {c(DIM, date)} {c(DIM, ts)}  {c(WHITE, f'${budget:.0f}')}"
                f"  {c(DIM, f'{delta_pct:.0f}% tick')}  {c(DIM, f'{msgs} msgs')}{delta_str}"
            )

        print()

    conn.close()
