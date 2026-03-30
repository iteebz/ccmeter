"""Read Claude Code OAuth token from OS keychain."""

import json
import subprocess
import sys


def get_oauth_token() -> str | None:
    """Extract the OAuth token Claude Code stores in the OS credential store."""
    if sys.platform == "darwin":
        return _macos_keychain()
    if sys.platform == "linux":
        return _linux_secret()
    return None


def _macos_keychain() -> str | None:
    """Read from macOS keychain where Claude Code stores credentials."""
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        # Claude Code stores JSON with the oauth token inside
        try:
            data = json.loads(raw)
            # Try common key patterns
            for key in ("accessToken", "access_token", "token"):
                if key in data:
                    return data[key]
            # If it's a nested structure, look deeper
            if "oauth" in data:
                oauth = data["oauth"]
                if isinstance(oauth, dict):
                    for key in ("accessToken", "access_token", "token"):
                        if key in oauth:
                            return oauth[key]
            return None
        except json.JSONDecodeError:
            # Might be a raw token string
            return raw if raw else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _linux_secret() -> str | None:
    """Read from libsecret / GNOME Keyring where Claude Code stores credentials on Linux."""
    try:
        result = subprocess.run(
            [
                "secret-tool",
                "lookup",
                "service",
                "Claude Code-credentials",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        try:
            data = json.loads(raw)
            for key in ("accessToken", "access_token", "token"):
                if key in data:
                    return data[key]
            return None
        except json.JSONDecodeError:
            return raw if raw else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
