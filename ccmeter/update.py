"""Version checking and self-update."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from ccmeter import __version__
from ccmeter.display import BOLD, DIM, WHITE, c, progress, progress_done

PYPI_URL = "https://pypi.org/pypi/ccmeter/json"
CACHE_PATH = Path.home() / ".ccmeter" / "version_check.json"
CHECK_INTERVAL = 3600  # 1 hour


def _fetch_pypi() -> dict[str, Any] | None:
    """Fetch full PyPI metadata (single request, reused for version + release)."""
    try:
        with urlopen(PYPI_URL, timeout=3) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _fetch_latest() -> str | None:
    data = _fetch_pypi()
    return data["info"]["version"] if data else None


def _find_release(data: dict[str, Any], version: str) -> dict[str, Any] | None:
    """Extract release file metadata from already-fetched PyPI data."""
    files = data.get("releases", {}).get(version, [])
    for f in files:
        if f["filename"].endswith(".whl"):
            return f
    for f in files:
        if f["filename"].endswith(".tar.gz"):
            return f
    return None


def _download(url: str, dest: Path, size: int | None):
    """Download a file with wave progress bar."""
    tty = sys.stdout.isatty()
    req = Request(url)
    with urlopen(req, timeout=30) as resp:
        total = size or int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 8192

        if tty and total:
            progress(total, 0, "download")

        with dest.open("wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if tty and total:
                    progress(total, min(downloaded, total), "download")

    if tty and total:
        progress_done("download")


def _read_cache() -> tuple[str | None, float]:
    try:
        with CACHE_PATH.open() as f:
            d = json.load(f)
        return d.get("latest"), d.get("checked_at", 0)
    except Exception:
        return None, 0


def _write_cache(latest: str):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps({"latest": latest, "checked_at": time.time()}))


def check_version(quiet: bool = False) -> str | None:
    """Check if a newer version exists. Returns latest version if outdated, None if current."""
    cached, checked_at = _read_cache()
    if time.time() - checked_at < CHECK_INTERVAL and cached:
        latest = cached
    else:
        latest = _fetch_latest()
        if latest:
            _write_cache(latest)

    if not latest or latest == __version__:
        return None

    if _version_tuple(latest) > _version_tuple(__version__):
        if not quiet:
            print(f"  {c(BOLD + WHITE, latest)} {c(DIM, 'available')}  →  {c(WHITE, 'ccmeter update')}")
        return latest
    return None


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _detect_installer() -> str:
    """Detect how ccmeter was installed by inspecting the runtime environment."""
    exe = sys.executable
    if "/pipx/" in exe:
        return "pipx"
    if "/uv/tools/" in exe:
        return "uv"
    return "pip"


def _install_from_file(path: Path, installer: str) -> int:
    """Install from a local wheel/sdist file, forcing cache bypass."""
    s = str(path)
    if installer == "pipx":
        return subprocess.run(["pipx", "install", s, "--force"]).returncode
    if installer == "uv":
        return subprocess.run(["uv", "tool", "install", s, "--force"]).returncode
    return subprocess.run([sys.executable, "-m", "pip", "install", s, "--force-reinstall"]).returncode


def run_update():
    """Check for updates and install latest version."""
    print(f"current: {__version__}")
    data = _fetch_pypi()
    if not data:
        print("could not reach PyPI")
        return

    latest = data["info"]["version"]
    _write_cache(latest)

    if _version_tuple(latest) <= _version_tuple(__version__):
        print("already up to date")
        return

    release = _find_release(data, latest)
    if not release:
        print(f"could not find release files for {latest}")
        return

    installer = _detect_installer()
    print(f"{__version__} -> {latest}")

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / release["filename"]
        _download(release["url"], dest, release.get("size"))

        print(f"installing via {installer}...")
        rc = _install_from_file(dest, installer)

    if rc == 0:
        print(f"updated to {latest}")
    else:
        print(f"install via {installer} failed (exit {rc})")
        if installer == "pip":
            print("try one of:")
            print("  pipx install ccmeter --force")
            print("  uv tool install ccmeter --force")
            print("  pip install -U ccmeter --break-system-packages")
        raise SystemExit(1)
