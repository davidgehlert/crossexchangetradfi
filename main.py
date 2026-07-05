"""Entry point: wires config -> clients -> engine and runs the polling loop.

Usage:
    python main.py

Behaviour is driven entirely by the .env file (see .env.example).
By default it runs in DRY_RUN paper mode and places no real orders.
"""

from __future__ import annotations

import logging
import math
import os
import signal
import sys
import time


from brokers import build_broker_for_pair
from brokers.paper_broker import PaperBroker
from config import Config
from engine.arbitrage import ArbitrageEngine
from engine.spread_monitor import SpreadRecorder, SpreadSketch, history_path
from exchanges.hyperliquid_client import HyperLiquidClient


def setup_logging() -> None:
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/arb.log"),
        ],
    )


_running = True


def _handle_signal(signum, _frame):
    global _running
    logging.getLogger("main").info("Received signal %s, shutting down...", signum)
    _running = False


def _feed_paper_broker(broker: PaperBroker, hl_mid: float, price_ratio: float, t0: float) -> None:
    """Give the paper broker a price so the engine can run end-to-end on macOS.

    Default: mirror HL (broker_norm == hl_mid) so no fake signals are produced.
    If PAPER_SIMULATE=true, overlay a slow sine wave on the spread so you can
    watch entries/exits trigger during local testing.
    """
    base = hl_mid / price_ratio if price_ratio else hl_mid
    if os.getenv("PAPER_SIMULATE", "false").strip().lower() in {"1", "true", "yes"}:
        amplitude_bps = float(os.getenv("PAPER_SIMULATE_BPS", "25"))
        period_s = float(os.getenv("PAPER_SIMULATE_PERIOD_S", "60"))
        wobble = math.sin((time.time() - t0) / period_s * 2 * math.pi)
        base = base * (1 + (amplitude_bps / 10_000) * wobble)
    broker.set_mark_price(base)



class _PairRunner:
    """One engine + its clients/loggers for a single symbol pair."""

    def __init__(self, cfg: Config, pair, primary: bool) -> None:
        self.pair = pair
        self.hl = HyperLiquidClient(
            account_address=cfg.hl_account_address,
            api_secret_key=cfg.hl_api_secret_key,
            symbol=pair.hl_symbol,
            network=cfg.hl_network,
            dry_run=cfg.dry_run,
            dex=pair.hl_dex,
        )
        self.broker = build_broker_for_pair(cfg, pair)
        self.engine = ArbitrageEngine(cfg, self.hl, self.broker)
        self.sketch = SpreadSketch(
            width=int(os.getenv("SKETCH_WIDTH", "60")),
            entry_bps=cfg.entry_spread_bps,
            exit_bps=cfg.exit_spread_bps,
        )
        self.recorder = SpreadRecorder(path=history_path(pair.key, primary=primary))


def main() -> int:
    setup_logging()
    log = logging.getLogger("main")
    cfg = Config.load()

    symbols = ", ".join(f"{p.label} ({p.hl_symbol})" for p in cfg.pairs)
    log.info(
        "Starting arb | dry_run=%s hl_network=%s broker=%s symbols=[%s]",
        cfg.dry_run, cfg.hl_network, cfg.broker, symbols,
    )
    if not cfg.dry_run:
        log.warning("LIVE TRADING ENABLED (DRY_RUN=false). Real orders will be placed.")

    runners = [
        _PairRunner(cfg, pair, primary=(i == 0)) for i, pair in enumerate(cfg.pairs)
    ]
    sketch_log = logging.getLogger("spread")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    t0 = time.time()
    while _running:
        for r in runners:
            try:
                if isinstance(r.broker, PaperBroker):
                    _feed_paper_broker(
                        r.broker, r.hl.get_mid_price(), r.pair.price_ratio, t0
                    )
                q = r.engine.step()
                r.recorder.record(q)
                sketch_log.info("%-6s %s", r.pair.key, r.sketch.update(q.spread_bps))
            except Exception as exc:
                log.exception("[%s] loop error: %s", r.pair.key, exc)
        time.sleep(cfg.poll_interval_seconds)

    for r in runners:
        r.broker.shutdown()
    log.info("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
