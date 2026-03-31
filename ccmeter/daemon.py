"""Install/uninstall ccmeter as a background daemon that survives restarts."""

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

LAUNCHD_LABEL = "com.ccmeter.poll"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"

SYSTEMD_UNIT = Path.home() / ".config" / "systemd" / "user" / "ccmeter.service"

WINDOWS_TASK_NAME = "ccmeter"


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
    if sys.platform == "win32":
        return _install_windows(ccmeter_bin)

    print(f"error: unsupported platform {sys.platform}", file=sys.stderr)
    return 1


def uninstall():
    """Remove ccmeter background daemon."""
    if sys.platform == "darwin":
        return _uninstall_launchd()
    if sys.platform == "linux":
        return _uninstall_systemd()
    if sys.platform == "win32":
        return _uninstall_windows()

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
            <key>EnvironmentVariables</key>
            <dict>
                <key>PYTHONUNBUFFERED</key>
                <string>1</string>
            </dict>
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


def _install_windows(ccmeter_bin: str) -> int:
    import os
    import tempfile

    log_dir = Path.home() / ".ccmeter"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "poll.log"
    err_path = log_dir / "poll.err"

    run_cmd = log_dir / "run.cmd"
    run_cmd.write_text(f'@echo off\nset PYTHONUNBUFFERED=1\n"{ccmeter_bin}" poll >> "{log_path}" 2>> "{err_path}"\n')

    # Launches with hidden window
    run_vbs = log_dir / "run.vbs"
    run_vbs.write_text(f'CreateObject("WScript.Shell").Run """{run_cmd}""", 0, True\n')

    xml = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-16"?>
        <Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
          <Triggers>
            <LogonTrigger>
              <Enabled>true</Enabled>
            </LogonTrigger>
          </Triggers>
          <Settings>
            <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
            <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
            <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
            <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
            <RestartOnFailure>
              <Interval>PT1M</Interval>
              <Count>999</Count>
            </RestartOnFailure>
          </Settings>
          <Actions Context="Author">
            <Exec>
              <Command>wscript.exe</Command>
              <Arguments>&quot;{run_vbs}&quot;</Arguments>
            </Exec>
          </Actions>
        </Task>
    """)

    fd, xml_path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(xml)
        result = subprocess.run(
            ["schtasks", "/create", "/tn", WINDOWS_TASK_NAME, "/xml", xml_path, "/f"],
            capture_output=True,
            text=True,
        )
    finally:
        Path(xml_path).unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"error creating scheduled task: {result.stderr}", file=sys.stderr)
        return 1

    subprocess.run(["schtasks", "/run", "/tn", WINDOWS_TASK_NAME], capture_output=True)

    print("ccmeter daemon installed and running")
    print(f"  task: {WINDOWS_TASK_NAME} (Task Scheduler)")
    print("  log:  ~/.ccmeter/poll.log")
    print("  stop: ccmeter uninstall")
    return 0


def _uninstall_windows() -> int:
    check = subprocess.run(
        ["schtasks", "/query", "/tn", WINDOWS_TASK_NAME],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        print("ccmeter daemon not installed")
        return 0

    subprocess.run(["schtasks", "/end", "/tn", WINDOWS_TASK_NAME], capture_output=True)
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", WINDOWS_TASK_NAME, "/f"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"error removing scheduled task: {result.stderr}", file=sys.stderr)
        return 1

    # Clean up wrapper scripts
    for name in ("run.cmd", "run.vbs"):
        p = Path.home() / ".ccmeter" / name
        if p.exists():
            p.unlink()

    print("ccmeter daemon stopped and removed")
    return 0
