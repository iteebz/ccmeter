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


def main():
    raise SystemExit(fncli.dispatch(["ccmeter", *sys.argv[1:]]))
