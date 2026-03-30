"""Produce anonymized, validated output for crowdsourced comparison."""

from __future__ import annotations

import hashlib
import io
import json
import platform
import sys
from contextlib import redirect_stdout
from typing import Any

from ccmeter import __version__
from ccmeter.report import cost_usd, run_report, tier_label


def _machine_hash() -> str:
    """One-way hash of hostname + username for dedup. Not reversible."""
    raw = f"{platform.node()}:{platform.os.getlogin()}"  # type: ignore[attr-defined]
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _validate_models(models: dict[str, Any]) -> bool:
    """Verify token counts x published rates = claimed budget."""
    for model, mdata in models.items():
        expected = cost_usd(mdata["avg_per_pct"], model)
        if abs(expected - mdata["avg_cost_per_pct"]) > 0.01:
            return False
    return True


def run_share(days: int = 30):
    """Generate and print anonymized share output."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        run_report(days=days, json_output=True)

    raw = buf.getvalue()
    if not raw.strip():
        return

    data = json.loads(raw)
    rate_tier = data.get("rate_limit_tier", "unknown")
    multiplier = data.get("multiplier", 1)

    output: dict[str, Any] = {
        "ccmeter": data.get("version", __version__),
        "machine": _machine_hash(),
        "tier": tier_label(rate_tier, multiplier),
        "multiplier": multiplier,
        "os": sys.platform,
        "days": days,
    }

    valid = True
    for bucket, bdata in data.get("buckets", {}).items():
        if not _validate_models(bdata.get("models", {})):
            valid = False

        models = {}
        for model, mdata in bdata.get("models", {}).items():
            models[model] = {
                "ticks": mdata["ticks"],
                "cost_per_pct": round(mdata["avg_cost_per_pct"], 4),
                "cache_ratio": round(mdata["avg_cache_ratio"], 4),
            }

        output[bucket] = {
            "ticks": bdata["ticks"],
            "capacity": round(bdata["capacity"], 2),
            "base": round(bdata["base_budget"], 2),
            "models": models,
        }

    output["valid"] = valid
    print(json.dumps(output, indent=2))
