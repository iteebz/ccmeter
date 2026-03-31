"""Display usage sample history."""

import json

from ccmeter.auth import fetch_account_id, get_credentials
from ccmeter.db import connect
from ccmeter.display import BOLD, CYAN, DIM, WHITE, YELLOW, c, hr, local_ts
from ccmeter.report import account_clause


def show_history(days: int = 7, json_output: bool = False):
    creds = get_credentials()
    account_id = fetch_account_id(creds.access_token) if creds else None
    af = account_clause(account_id)
    conn = connect()
    rows = conn.execute(
        f"SELECT ts, bucket, utilization, resets_at FROM usage_samples "
        f"WHERE {af()} AND ts > datetime('now', ? || ' days') ORDER BY ts DESC",
        (f"-{days}",),
    ).fetchall()
    conn.close()

    if not rows:
        print(f"no samples in the last {days} days. run: ccmeter poll")
        return

    if json_output:
        print(json.dumps([dict(r) for r in rows], indent=2))
        return

    print()
    print(f"  {c(BOLD + WHITE, 'history')}  {c(DIM, f'{len(rows)} samples over {days}d')}")
    print(f"  {hr()}")

    prev_date = ""
    for r in rows:
        local = local_ts(r["ts"])
        date = local[:10]
        time_str = local[11:16]

        if date != prev_date:
            if prev_date:
                print()
            print(f"  {c(DIM, date)}")
            prev_date = date

        bucket = r["bucket"]
        util = r["utilization"]
        color = YELLOW if util > 80 else CYAN
        print(f"    {c(DIM, time_str)}  {bucket:<20} {c(color, f'{util:5.1f}%')}")

    print()
