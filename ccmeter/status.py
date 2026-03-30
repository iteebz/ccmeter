"""Show current collection status."""

from ccmeter.db import DB_PATH, connect


def show_status():
    if not DB_PATH.exists():
        print("no data collected yet. run: cc-meter poll")
        return

    conn = connect()

    total = conn.execute("SELECT COUNT(*) as n FROM usage_samples").fetchone()["n"]
    buckets = conn.execute("SELECT DISTINCT bucket FROM usage_samples ORDER BY bucket").fetchall()
    latest = conn.execute("SELECT ts FROM usage_samples ORDER BY ts DESC LIMIT 1").fetchone()
    oldest = conn.execute("SELECT ts FROM usage_samples ORDER BY ts ASC LIMIT 1").fetchone()

    conn.close()

    print("cc-meter status")
    print(f"  db: {DB_PATH}")
    print(f"  samples: {total}")
    print(f"  buckets: {', '.join(r['bucket'] for r in buckets)}")
    if oldest and latest:
        print(f"  range: {oldest['ts'][:16]} → {latest['ts'][:16]}")
