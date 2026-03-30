"""Read Claude Code OAuth credentials from OS keychain."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class Credentials:
    access_token: str
    refresh_token: str | None
    expires_at: str | None
    subscription_type: str | None
    rate_limit_tier: str | None


def get_credentials() -> Credentials | None:
    """Extract OAuth credentials Claude Code stores in the OS credential store."""
    if sys.platform == "darwin":
        return _macos_keychain()
    if sys.platform == "linux":
        return _linux_secret()
    if sys.platform == "win32":
        return _windows_credential()
    return None


def _parse_credentials(raw: str) -> Credentials | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    oauth = data.get("claudeAiOauth")
    if not oauth or not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    if not token:
        return None
    return Credentials(
        access_token=token,
        refresh_token=oauth.get("refreshToken"),
        expires_at=oauth.get("expiresAt"),
        subscription_type=oauth.get("subscriptionType"),
        rate_limit_tier=oauth.get("rateLimitTier"),
    )


def _run_keychain(args: list[str]) -> Credentials | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None
        return _parse_credentials(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _macos_keychain() -> Credentials | None:
    import os

    user = os.environ.get("USER", "")
    return _run_keychain(["security", "find-generic-password", "-a", user, "-s", "Claude Code-credentials", "-w"])


def _linux_secret() -> Credentials | None:
    return _run_keychain(["secret-tool", "lookup", "service", "Claude Code-credentials"])


def _windows_credential() -> Credentials | None:
    """Read Claude Code credentials from ~/.claude/.credentials.json."""
    from pathlib import Path

    cred_file = Path.home() / ".claude" / ".credentials.json"
    try:
        return _parse_credentials(cred_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
