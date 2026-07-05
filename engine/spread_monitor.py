"""Spread visualization helpers.

Two ways to "sketch" the HyperLiquid vs. broker spread:

* ``SpreadSketch`` renders a live single-line unicode sparkline of the recent
  spread (in bps) straight into the terminal/log — no extra dependencies.
* ``SpreadRecorder`` appends every observation to a CSV so you can later draw a
  full chart with ``plot_spread.py`` (matplotlib).
"""

from __future__ import annotations

import csv
import os
from collections import deque
from datetime import datetime, timedelta, timezone

_BLOCKS = "▁▂▃▄▅▆▇█"


def history_path(key: str, primary: bool = False) -> str:
    """CSV path for a symbol's spread history.

    The primary pair keeps the legacy ``state/spread_history.csv`` filename so
    existing history is preserved; other symbols get a per-key file.
    """
    if primary:
        return "state/spread_history.csv"
    return f"state/spread_history_{key}.csv"


def load_history(path: str = "state/spread_history.csv", since=None) -> list[dict]:
    """Read recorded spread rows, optionally only those at/after ``since``.

    Returns a list of dicts with parsed types: ``ts`` (datetime), ``hl_mid``,
    ``broker_mid``, ``broker_norm``, ``spread_bps`` (floats), ``funding``
    (float | None). Malformed rows are skipped.
    """
    if not os.path.exists(path):
        return []
    rows: list[dict] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                ts = datetime.fromisoformat(row["ts"])
            except (ValueError, KeyError):
                continue
            if since is not None and ts < since:
                continue
            try:
                rows.append(
                    {
                        "ts": ts,
                        "hl_mid": float(row["hl_mid"]),
                        "broker_mid": float(row["broker_mid"]),
                        "broker_norm": float(row["broker_norm"]),
                        "spread_bps": float(row["spread_bps"]),
                        "funding": float(row["funding"]) if row.get("funding") else None,
                    }
                )
            except (ValueError, KeyError):
                continue
    return rows


_HISTORY_FIELDS = ["ts", "hl_mid", "broker_mid", "broker_norm", "spread_bps", "funding"]


def write_history(rows: list[dict], path: str = "state/spread_history.csv") -> None:
    """Rewrite the whole CSV from ``rows`` (dicts), sorted ascending by ``ts``.

    Each row dict needs ts (datetime), hl_mid, broker_mid, broker_norm,
    spread_bps (floats) and funding (float | None).
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    ordered = sorted(rows, key=lambda r: r["ts"])
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_HISTORY_FIELDS)
        for r in ordered:
            ts = r["ts"]
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            w.writerow(
                [
                    ts_str,
                    f"{r['hl_mid']:.6f}",
                    f"{r['broker_mid']:.6f}",
                    f"{r['broker_norm']:.6f}",
                    f"{r['spread_bps']:.4f}",
                    "" if r.get("funding") is None else f"{r['funding']:.8f}",
                ]
            )


def downsample(rows: list, max_points: int) -> list:
    """Evenly thin a list down to at most ``max_points`` (keeps the last point)."""
    n = len(rows)
    if max_points <= 0 or n <= max_points:
        return rows
    step = n / max_points
    out = [rows[int(i * step)] for i in range(max_points)]
    if out[-1] is not rows[-1]:
        out[-1] = rows[-1]
    return out


def sparkline(values) -> str:
    """Return a unicode sparkline for a sequence of floats."""
    values = list(values)
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    out = []
    for v in values:
        idx = int((v - lo) / span * (len(_BLOCKS) - 1))
        out.append(_BLOCKS[idx])
    return "".join(out)


class SpreadSketch:
    """Keeps a rolling window of spreads and renders a one-line live sketch."""

    def __init__(self, width: int = 60, entry_bps: float = 0.0, exit_bps: float = 0.0):
        self.width = width
        self.entry_bps = entry_bps
        self.exit_bps = exit_bps
        self._buf: deque[float] = deque(maxlen=width)

    def update(self, spread_bps: float) -> str:
        self._buf.append(spread_bps)
        spark = sparkline(self._buf)
        lo = min(self._buf)
        hi = max(self._buf)
        # Mark whether we're currently outside the entry band.
        zone = "ENTRY" if abs(spread_bps) >= self.entry_bps else (
            "exit" if abs(spread_bps) <= self.exit_bps else "..."
        )
        return (
            f"spread {spread_bps:+7.2f}bps |{spark}| "
            f"min={lo:+.1f} max={hi:+.1f} [{zone}]"
        )


class SpreadRecorder:
    """Append spread observations to a CSV for later plotting."""

    FIELDS = ["ts", "hl_mid", "broker_mid", "broker_norm", "spread_bps", "funding"]

    def __init__(self, path: str = "state/spread_history.csv"):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(self.FIELDS)

    def record(self, q) -> None:
        with open(self.path, "a", newline="") as f:
            csv.writer(f).writerow(
                [
                    datetime.now(timezone.utc).isoformat(),
                    f"{q.hl_mid:.6f}",
                    f"{q.broker_mid:.6f}",
                    f"{q.broker_norm:.6f}",
                    f"{q.spread_bps:.4f}",
                    "" if q.funding is None else f"{q.funding:.8f}",
                ]
            )
