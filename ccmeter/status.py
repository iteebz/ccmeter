"""Show current collection status."""

from ccmeter.db import DB_PATH, connect
from ccmeter.display import BOLD, CYAN, DIM, GREEN, WHITE, YELLOW, c, hr


def show_status():
    if not DB_PATH.exists():
        print("no data collected yet. run: ccmeter poll")
        return

    conn = connect()

    total = conn.execute("SELECT COUNT(*) as n FROM usage_samples").fetchone()["n"]
    latest = conn.execute("SELECT ts FROM usage_samples ORDER BY ts DESC LIMIT 1").fetchone()
    oldest = conn.execute("SELECT ts FROM usage_samples ORDER BY ts ASC LIMIT 1").fetchone()

    # per-bucket current state
    current = conn.execute(
        """SELECT bucket, utilization, ts FROM usage_samples
           WHERE id IN (SELECT MAX(id) FROM usage_samples GROUP BY bucket)
           ORDER BY bucket"""
    ).fetchall()

    conn.close()

    print()
    print(f"  {c(BOLD + WHITE, 'ccmeter status')}")
    print(f"  {hr()}")
    print(f"  {c(DIM, 'db')}       {c(DIM, str(DB_PATH))}")
    print(f"  {c(DIM, 'samples')}  {c(WHITE, total)}")
    if oldest and latest:
        print(f"  {c(DIM, 'range')}    {c(DIM, oldest['ts'][:16])} → {c(DIM, latest['ts'][:16])}")
    print()

    if current:
        for r in current:
            util = r["utilization"]
            color = GREEN if util < 50 else YELLOW if util < 80 else CYAN
            print(f"    {r['bucket']:<22} {c(color, f'{util:5.1f}%')}  {c(DIM, r['ts'][:16])}")
        print()
