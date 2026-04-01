"""Local config: ~/.ccmeter/config.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path.home() / ".ccmeter" / "config.json"


def load() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n")


def pinned_account() -> str | None:
    return load().get("account_id")


def pin_account(account_id: str) -> None:
    cfg = load()
    cfg["account_id"] = account_id
    save(cfg)


def unpin_account() -> None:
    cfg = load()
    cfg.pop("account_id", None)
    save(cfg)
