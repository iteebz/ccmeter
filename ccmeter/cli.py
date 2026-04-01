"""ccmeter CLI."""

import sys

import fncli

from ccmeter import __version__


@fncli.cli()
def version():
    """print version"""
    print(__version__)


@fncli.cli("ccmeter")
def poll(interval: int = 120, once: bool = False):
    """poll usage API and record samples to local sqlite"""
    from ccmeter.poll import run_poll

    run_poll(interval=interval, once=once)


@fncli.cli("ccmeter")
def report(days: int = 30, json: bool = False, recache: bool = False):
    """calibration report: budget per window and trend. --json for export. --recache to rebuild scan cache"""
    from ccmeter.report import run_report

    run_report(days=days, json_output=json, recache=recache)
    if not json:
        from ccmeter.update import check_version

        check_version()


@fncli.cli("ccmeter")
def share(days: int = 30):
    """anonymized output for crowdsourced comparison"""
    from ccmeter.share import run_share

    run_share(days=days)


@fncli.cli("ccmeter")
def history(days: int = 7, json: bool = False):
    """show raw usage sample history"""
    from ccmeter.history import show_history

    show_history(days=days, json_output=json)


@fncli.cli("ccmeter")
def trend(days: int = 30, recache: bool = False):
    """sparkline chart of budget over time from calibration ticks"""
    from ccmeter.trend import show_trend

    show_trend(days=days, recache=recache)


@fncli.cli("ccmeter")
def status():
    """show current usage and collection stats"""
    from ccmeter.status import show_status

    show_status()
    from ccmeter.update import check_version

    check_version()


@fncli.cli("ccmeter")
def account(pin: bool = False, unpin: bool = False):
    """show account info. --pin to lock to current account. --unpin to clear"""
    from ccmeter.auth import fetch_account_id, get_credentials
    from ccmeter.config import pin_account, pinned_account, unpin_account
    from ccmeter.db import connect
    from ccmeter.display import BOLD, CYAN, DIM, GREEN, WHITE, YELLOW, c, hr

    if unpin:
        unpin_account()
        print(f"  {c(GREEN, 'unpinned')} — polling all accounts")
        return

    creds = get_credentials()
    if not creds:
        print("error: no credentials found", file=sys.stderr)
        raise SystemExit(1)

    active_id = fetch_account_id(creds.access_token)
    tier = creds.subscription_type or "unknown"
    rate_tier = creds.rate_limit_tier or "unknown"
    pinned = pinned_account()

    if pin:
        if not active_id:
            print("error: could not resolve account id", file=sys.stderr)
            raise SystemExit(1)
        pin_account(active_id)
        print(f"  {c(GREEN, 'pinned')} to {c(WHITE, active_id[:8])}…")
        print("  poller will skip data from other accounts")
        return

    print()
    print(f"  {c(BOLD + WHITE, 'account')}")
    print(f"  {hr()}")
    print(f"  {c(DIM, 'active')}   {c(WHITE, active_id[:8] + '…') if active_id else c(YELLOW, 'unknown')}")
    print(f"  {c(DIM, 'plan')}     {c(CYAN, tier)}")
    print(f"  {c(DIM, 'tier')}     {c(DIM, rate_tier)}")
    if pinned:
        is_match = active_id == pinned
        pin_color = GREEN if is_match else YELLOW
        pin_label = "active" if is_match else "mismatch — polling paused"
        print(f"  {c(DIM, 'pinned')}   {c(pin_color, pinned[:8] + '…')} {c(DIM, pin_label)}")
    else:
        print(f"  {c(DIM, 'pinned')}   {c(DIM, 'none — tracking all accounts')}")

    # Per-account sample counts
    conn = connect()
    rows = conn.execute(
        "SELECT account_id, tier, COUNT(*) as n, MAX(ts) as last_ts FROM usage_samples GROUP BY account_id"
    ).fetchall()
    conn.close()

    if rows:
        print()
        for r in rows:
            aid = r["account_id"] or "unknown"
            short = aid[:8] + "…" if len(aid) > 8 else aid
            marker = " ←" if aid == active_id else ""
            n = r["n"]
            t = r["tier"] or "?"
            print(f"    {c(WHITE, short)}  {c(DIM, t)}  {c(DIM, f'{n} samples')}{marker}")
    print()


@fncli.cli("ccmeter")
def update():
    """check for updates and install latest version"""
    from ccmeter.update import run_update

    run_update()


@fncli.cli("ccmeter")
def install():
    """install ccmeter as a background daemon (survives restarts)"""
    from ccmeter.daemon import install as do_install

    raise SystemExit(do_install())


@fncli.cli("ccmeter")
def uninstall():
    """stop and remove the background daemon"""
    from ccmeter.daemon import uninstall as do_uninstall

    raise SystemExit(do_uninstall())


def _print_help():
    from ccmeter.display import BOLD, DIM, RESET, WHITE, c, gradient_text, hr

    print()
    print(f"  {BOLD}{gradient_text('ccmeter')}{RESET} {c(DIM, __version__)}")
    tagline = "measure your actual limits"
    print(f"  {c(DIM, tagline)}")
    print()

    for cmd, desc in [
        ("install", "background daemon — set it and forget it"),
        ("report", "your budget in dollars, per window"),
        ("status", "current utilization and collection health"),
        ("trend", "budget over time as a sparkline chart"),
        ("share", "anonymized data for crowdsourced comparison"),
        ("account", "show account info and pin/unpin"),
        ("history", "raw usage samples"),
    ]:
        print(f"  {c(WHITE, f'{cmd:<10}')} {c(DIM, desc)}")

    print()
    print(f"  {hr(40)}")
    print()

    for cmd, desc in [
        ("update", "check for and install updates"),
        ("uninstall", "remove the background daemon"),
    ]:
        print(f"  {c(DIM, f'{cmd:<10} {desc}')}")

    print()
    print(f"  {c(DIM, 'ccmeter <command> --help for details')}")
    print()


def main():
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    args = sys.argv[1:]
    if not args or args == ["--help"] or args == ["-h"]:
        _print_help()
        from ccmeter.update import check_version

        check_version()
        raise SystemExit(0)
    raise SystemExit(fncli.dispatch(["ccmeter", *args]))
