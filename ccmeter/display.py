"""Display primitives: colors, progress, formatting."""

from __future__ import annotations

import math
import sys
import time
from datetime import datetime, timezone

# ANSI codes
DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
WHITE = "\033[37m"
PURPLE = "\033[38;2;160;130;220m"
PINK = "\033[38;2;210;140;190m"
RESET = "\033[0m"
RULE = "\033[38;2;60;60;80m"

# Wave bar
WIDTH = 40
_GRAD_START = (140, 120, 220)
_GRAD_END = (210, 140, 190)


def c(code: str, text: str | int | float) -> str:
    """Colorize text, stripping ANSI if not a TTY."""
    return f"{code}{text}{RESET}" if sys.stdout.isatty() else str(text)


def hr(width: int = 64) -> str:
    return c(RULE, "─" * width)


def pl(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def human(n: int | float) -> str:
    """Format large numbers: 5678315 → 5.7M, 57080 → 57k, 86 → 86."""
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 10_000:
        return f"{n / 1_000:.0f}k"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    if isinstance(n, float):
        return f"{n:.1f}"
    return str(n)


def local_ts(iso_ts: str) -> str:
    """Convert UTC ISO timestamp to local date + time: '2026-03-30 19:23'."""
    utc = datetime.fromisoformat(iso_ts)
    if utc.tzinfo is None:
        utc = utc.replace(tzinfo=timezone.utc)
    local = utc.astimezone()
    return local.strftime("%Y-%m-%d %H:%M")


def ago(iso_ts: str) -> str:
    """Convert ISO timestamp to relative time: '2h ago', '3d ago'."""
    then = datetime.fromisoformat(iso_ts)
    delta = datetime.now(tz=timezone.utc) - then
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def gradient_text(text: str) -> str:
    """Apply the purple→pink gradient across each character."""
    n = len(text)
    return "".join(f"{gradient(i, n)}{ch}" for i, ch in enumerate(text)) + RESET


def gradient(i: int, width: int) -> str:
    t = i / max(width - 1, 1)
    r = int(_GRAD_START[0] + (_GRAD_END[0] - _GRAD_START[0]) * t)
    g = int(_GRAD_START[1] + (_GRAD_END[1] - _GRAD_START[1]) * t)
    b = int(_GRAD_START[2] + (_GRAD_END[2] - _GRAD_START[2]) * t)
    return f"\033[38;2;{r};{g};{b}m"


def _wave(pct: float, width: int = WIDTH) -> str:
    if pct >= 1.0:
        return "".join(f"{gradient(i, width)}█" for i in range(width)) + RESET
    filled = int(pct * width)
    t = time.time()
    heights = " ▁▂▃▄▅▆▇█"
    bar = ""
    for i in range(width):
        if i < filled:
            wave = math.sin(t * 6 + i * 0.4) * 0.3 + 0.7
            bar += f"{gradient(i, width)}{heights[int(wave * 8)]}"
        else:
            bar += f"{RESET} "
    return bar + RESET


def wave_line(pct: float, label: str = "", width: int = WIDTH) -> str:
    bar = _wave(pct, width)
    pct_str = f"{int(pct * 100):>3}%"
    prefix = f"  {label}  " if label else "  "
    return f"{prefix}{bar}  {pct_str}"


def progress(total: int, current: int, label: str = "") -> None:
    pct = current / total if total else 0.0
    line = wave_line(pct, label)
    sys.stdout.write(f"\r\033[2K{line}")
    sys.stdout.flush()


def progress_done(label: str = "") -> None:
    line = wave_line(1.0, label)
    sys.stdout.write(f"\r\033[2K{line}\n")
    sys.stdout.flush()
