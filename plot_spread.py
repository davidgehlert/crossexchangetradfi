"""Render a chart of the recorded HyperLiquid vs. broker spread.

Reads the CSV written by SpreadRecorder (default state/spread_history.csv) and
saves a PNG (default state/spread.png) showing the spread in bps over time with
the entry/exit threshold bands drawn in.

Usage:
    python plot_spread.py
    python plot_spread.py --csv state/spread_history.csv --out state/spread.png
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime

from config import Config


def load(path: str):
    ts, spread, hl, broker_norm = [], [], [], []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                ts.append(datetime.fromisoformat(row["ts"]))
                spread.append(float(row["spread_bps"]))
                hl.append(float(row["hl_mid"]))
                broker_norm.append(float(row["broker_norm"]))
            except (ValueError, KeyError):
                continue
    return ts, spread, hl, broker_norm


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot the recorded spread.")
    parser.add_argument("--csv", default="state/spread_history.csv")
    parser.add_argument("--out", default="state/spread.png")
    args = parser.parse_args()

    import matplotlib

    matplotlib.use("Agg")  # headless: write a file, no display needed
    import matplotlib.pyplot as plt

    ts, spread, hl, broker_norm = load(args.csv)
    if not ts:
        print(f"No data in {args.csv} yet. Run the bot first.")
        return 1

    cfg = Config.load()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True)

    # Top: the two price feeds.
    ax1.plot(ts, hl, label="HyperLiquid mid", linewidth=1.2)
    ax1.plot(ts, broker_norm, label="Broker mid (normalized)", linewidth=1.2)
    ax1.set_ylabel("Price")
    ax1.set_title(f"{cfg.hl_symbol} — HyperLiquid vs. broker")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    # Bottom: the spread in bps with entry/exit bands.
    ax2.plot(ts, spread, color="black", linewidth=1.0, label="spread (bps)")
    ax2.axhline(0, color="gray", linewidth=0.8)
    for sign in (1, -1):
        ax2.axhline(sign * cfg.entry_spread_bps, color="red", linestyle="--",
                    linewidth=0.9, label="entry" if sign == 1 else None)
        ax2.axhline(sign * cfg.exit_spread_bps, color="green", linestyle=":",
                    linewidth=0.9, label="exit" if sign == 1 else None)
    ax2.fill_between(ts, -cfg.exit_spread_bps, cfg.exit_spread_bps,
                     color="green", alpha=0.08)
    ax2.set_ylabel("Spread (bps)")
    ax2.set_xlabel("Time (UTC)")
    ax2.legend(loc="upper left")
    ax2.grid(True, alpha=0.3)

    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(args.out, dpi=130)
    print(f"Wrote {args.out} ({len(ts)} points)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
