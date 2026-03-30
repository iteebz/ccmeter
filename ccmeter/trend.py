"""Per-tick budget chart, computed from source data."""

from ccmeter.db import connect
from ccmeter.display import BOLD, DIM, RESET, WHITE, _color, c, hr, local_ts
from ccmeter.report import BUCKET_LABELS, calibrate_bucket
from ccmeter.scan import scan

CHART_WIDTH = 60
CHART_HEIGHT = 15

# Braille: each character is a 2x4 dot grid. Offset 0x2800.
# Dot positions: col 0 = bits 0,1,2,6  col 1 = bits 3,4,5,7
# Row 0 (top) to row 3 (bottom)
_BRAILLE_BASE = 0x2800
_DOT_MAP = [
    [0x01, 0x08],  # row 0
    [0x02, 0x10],  # row 1
    [0x04, 0x20],  # row 2
    [0x40, 0x80],  # row 3
]


def _chart(values: list[float], width: int = CHART_WIDTH, height: int = CHART_HEIGHT) -> list[str]:
    """Render a braille line chart. Returns rows top to bottom."""
    if not values:
        return []

    lo, hi = min(values), max(values)
    span = hi - lo if hi > lo else 1.0

    # Each character is 2 dots wide, 4 dots tall
    dot_w = width * 2
    dot_h = height * 4

    # Interpolate values to dot-width resolution
    n = len(values)
    points: list[int] = []
    for i in range(dot_w):
        t = i / max(dot_w - 1, 1) * (n - 1)
        lo_idx = int(t)
        hi_idx = min(lo_idx + 1, n - 1)
        frac = t - lo_idx
        v = values[lo_idx] * (1 - frac) + values[hi_idx] * frac
        y = int((v - lo) / span * (dot_h - 1) + 0.5)
        points.append(y)

    # Draw lines between consecutive points into a dot grid
    grid: set[tuple[int, int]] = set()
    for i in range(len(points)):
        grid.add((i, points[i]))
        if i > 0:
            y0, y1 = points[i - 1], points[i]
            steps = max(abs(y1 - y0), 1)
            for s in range(steps + 1):
                t = s / steps
                y = int(y0 + (y1 - y0) * t + 0.5)
                x = int((i - 1) + t + 0.5)
                grid.add((x, y))

    # Render to braille characters
    rows = []
    for row in range(height):
        chars = []
        for col in range(width):
            code = _BRAILLE_BASE
            for dr in range(4):
                for dc in range(2):
                    dx = col * 2 + dc
                    # Flip y: top of chart = high values
                    dy = (height - 1 - row) * 4 + (3 - dr)
                    if (dx, dy) in grid:
                        code |= _DOT_MAP[dr][dc]
            color = _color(col, width)
            chars.append(f"{color}{chr(code)}{RESET}")
        rows.append("".join(chars))
    return rows


def show_trend(days: int = 30, recache: bool = False):
    result = scan(days=days, recache=recache)
    if not result.events:
        print("no token events found. run: ccmeter report")
        return

    conn = connect()
    buckets_row = conn.execute("SELECT DISTINCT bucket FROM usage_samples").fetchall()
    buckets = [r["bucket"] for r in buckets_row]

    print()
    print(f"  {c(BOLD + WHITE, 'trend')}  {c(DIM, f'{days}d')}")

    for bucket in buckets:
        cals = calibrate_bucket(bucket, result.events, conn)
        if not cals:
            continue

        window = BUCKET_LABELS.get(bucket) or bucket
        budgets = [cal["cost_per_pct"] * 100 for cal in cals]
        lo, hi = min(budgets), max(budgets)
        avg = sum(budgets) / len(budgets)

        print(f"  {hr()}")
        print(f"  {c(BOLD + WHITE, window)}  {c(DIM, f'{len(cals)} ticks')}  {c(DIM, f'avg ${avg:.0f}')}")
        print()

        rows = _chart(budgets)
        for i, row in enumerate(rows):
            label = ""
            if i == 0:
                label = c(DIM, f" ${hi:.0f}")
            elif i == len(rows) - 1:
                label = c(DIM, f" ${lo:.0f}")
            print(f"    {row}{label}")

        first = local_ts(cals[0]["t0"])
        last = local_ts(cals[-1]["t0"])
        padding = CHART_WIDTH - len(first[5:]) - len(last[5:])
        print(f"    {c(DIM, first[5:])}{' ' * max(padding, 1)}{c(DIM, last[5:])}")
        print()

    conn.close()
