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
def history(days: int = 7, json: bool = False):
    """show usage sample history"""
    from ccmeter.history import show_history

    show_history(days=days, json_output=json)


@fncli.cli("ccmeter")
def calibrate(bucket: str = "five_hour"):
    """calculate what 1% actually means in tokens"""
    from ccmeter.calibrate import run_calibrate

    run_calibrate(bucket=bucket)


@fncli.cli("ccmeter")
def status():
    """show current usage and collection stats"""
    from ccmeter.status import show_status

    show_status()


def main():
    fncli.run(["ccmeter", *sys.argv[1:]])
