"""Calculate what 1% actually means in tokens by cross-referencing JSONL session data."""

from ccmeter.db import connect


def run_calibrate(bucket: str = "five_hour"):
    conn = connect()

    # Get consecutive sample pairs where utilization increased
    rows = conn.execute(
        """
        SELECT
            s1.ts as t0, s2.ts as t1,
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
    conn.close()

    if not rows:
        print(f"no calibration data for {bucket} yet.")
        print("need at least two samples where utilization increased.")
        print("run: cc-meter poll")
        return

    print(f"calibration data for {bucket}")
    print(f"{'window':<40} {'delta':>6} {'duration':>10}")
    print("-" * 60)

    for r in rows:
        t0, t1 = r["t0"], r["t1"]
        delta = r["delta_pct"]
        window = f"{t0[:16]} → {t1[:16]}"
        print(f"{window:<40} {delta:>5.0f}% {'':>10}")

    print(f"\n{len(rows)} tick(s) recorded")
    print("\ntoken cross-referencing requires JSONL session parsing (coming soon)")
    print("for now: correlate these windows against your Claude Code session logs")
    print(f"  logs: ~/.claude/projects/*/")
