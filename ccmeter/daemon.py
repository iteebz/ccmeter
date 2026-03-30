"""Install/uninstall ccmeter as a background daemon that survives restarts."""

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

LAUNCHD_LABEL = "com.ccmeter.poll"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"

SYSTEMD_UNIT = Path.home() / ".config" / "systemd" / "user" / "ccmeter.service"


def install():
    """Install ccmeter as a background daemon."""
    ccmeter_bin = shutil.which("ccmeter")
    if not ccmeter_bin:
        print("error: ccmeter not found in PATH", file=sys.stderr)
        print("install first: pip install ccmeter", file=sys.stderr)
        return 1

    if sys.platform == "darwin":
        return _install_launchd(ccmeter_bin)
    if sys.platform == "linux":
        return _install_systemd(ccmeter_bin)

    print(f"error: unsupported platform {sys.platform}", file=sys.stderr)
    return 1


def uninstall():
    """Remove ccmeter background daemon."""
    if sys.platform == "darwin":
        return _uninstall_launchd()
    if sys.platform == "linux":
        return _uninstall_systemd()

    print(f"error: unsupported platform {sys.platform}", file=sys.stderr)
    return 1


def _install_launchd(ccmeter_bin: str) -> int:
    plist = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{LAUNCHD_LABEL}</string>
            <key>ProgramArguments</key>
            <array>
                <string>{ccmeter_bin}</string>
                <string>poll</string>
            </array>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <true/>
            <key>StandardOutPath</key>
            <string>{Path.home()}/.ccmeter/poll.log</string>
            <key>StandardErrorPath</key>
            <string>{Path.home()}/.ccmeter/poll.err</string>
        </dict>
        </plist>
    """)

    LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHD_PLIST.write_text(plist)

    # unload first if already loaded
    subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(LAUNCHD_PLIST)], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"error loading launchd plist: {result.stderr}", file=sys.stderr)
        return 1

    print("ccmeter daemon installed and running")
    print(f"  plist: {LAUNCHD_PLIST}")
    print("  log:   ~/.ccmeter/poll.log")
    print("  stop:  ccmeter uninstall")
    return 0


def _uninstall_launchd() -> int:
    if not LAUNCHD_PLIST.exists():
        print("ccmeter daemon not installed")
        return 0

    subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)], capture_output=True)
    LAUNCHD_PLIST.unlink()
    print("ccmeter daemon stopped and removed")
    return 0


def _install_systemd(ccmeter_bin: str) -> int:
    unit = textwrap.dedent(f"""\
        [Unit]
        Description=ccmeter usage polling daemon
        After=network.target

        [Service]
        ExecStart={ccmeter_bin} poll
        Restart=always
        RestartSec=30

        [Install]
        WantedBy=default.target
    """)

    SYSTEMD_UNIT.parent.mkdir(parents=True, exist_ok=True)
    SYSTEMD_UNIT.write_text(unit)

    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    result = subprocess.run(["systemctl", "--user", "enable", "--now", "ccmeter"], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"error enabling systemd unit: {result.stderr}", file=sys.stderr)
        return 1

    print("ccmeter daemon installed and running")
    print(f"  unit:   {SYSTEMD_UNIT}")
    print("  status: systemctl --user status ccmeter")
    print("  stop:   ccmeter uninstall")
    return 0


def _uninstall_systemd() -> int:
    if not SYSTEMD_UNIT.exists():
        print("ccmeter daemon not installed")
        return 0

    subprocess.run(["systemctl", "--user", "disable", "--now", "ccmeter"], capture_output=True)
    SYSTEMD_UNIT.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    print("ccmeter daemon stopped and removed")
    return 0
