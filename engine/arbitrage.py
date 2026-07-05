"""Delta-neutral cross-venue arbitrage engine for HyperLiquid vs. a CFD broker.

Logic
-----
Each loop we read the mid price on HyperLiquid (the perp) and on the broker
(the SPY / US500 CFD), normalise the broker price onto the HL scale via
``price_ratio`` and compute the spread in basis points:

    spread_bps = (hl - broker_norm) / mid_ref * 10_000

* If spread_bps > +entry  -> HL rich  -> SHORT HL, LONG broker.
* If spread_bps < -entry  -> HL cheap -> LONG HL, SHORT broker.
* While a hedge is open, close both legs once |spread_bps| < exit.

Both legs are sized equally (``position_size``) so the book stays roughly
delta-neutral; profit comes from the spread reverting (plus any favourable
HyperLiquid funding while the position is held).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

log = logging.getLogger("engine")


class Side(Enum):
    FLAT = "flat"
    HL_SHORT_BROKER_LONG = "hl_short_broker_long"  # entered when HL was rich
    HL_LONG_BROKER_SHORT = "hl_long_broker_short"  # entered when HL was cheap


@dataclass
class Quote:
    hl_mid: float
    broker_mid: float
    broker_norm: float
    mid_ref: float
    spread_bps: float
    funding: float | None


class ArbitrageEngine:
    def __init__(self, cfg, hl_client, broker):
        self.cfg = cfg
        self.hl = hl_client
        self.broker = broker

    # ----- quoting --------------------------------------------------------
    def quote(self) -> Quote:
        hl_mid = self.hl.get_mid_price()
        broker_mid = self.broker.get_mid_price()
        broker_norm = broker_mid * self.cfg.price_ratio
        mid_ref = (hl_mid + broker_norm) / 2.0 if (hl_mid and broker_norm) else 0.0
        spread_bps = ((hl_mid - broker_norm) / mid_ref * 10_000) if mid_ref else 0.0
        return Quote(
            hl_mid=hl_mid,
            broker_mid=broker_mid,
            broker_norm=broker_norm,
            mid_ref=mid_ref,
            spread_bps=spread_bps,
            funding=self.hl.get_funding_rate(),
        )

    # ----- state ----------------------------------------------------------
    def current_side(self) -> Side:
        hl_pos = self.hl.get_position()
        if hl_pos is None or hl_pos.size == 0:
            return Side.FLAT
        return Side.HL_SHORT_BROKER_LONG if hl_pos.size < 0 else Side.HL_LONG_BROKER_SHORT

    def open_count(self) -> int:
        return 0 if self.current_side() is Side.FLAT else 1

    # ----- pnl / risk -----------------------------------------------------
    def unrealized_pnl(self, q: Quote) -> float:
        """Rough combined unrealized PnL in quote currency across both legs."""
        pnl = 0.0
        hl_pos = self.hl.get_position()
        if hl_pos and hl_pos.entry_px:
            pnl += hl_pos.size * (q.hl_mid - hl_pos.entry_px)
        br_pos = self.broker.get_position()
        if br_pos and br_pos.entry_px:
            pnl += br_pos.size * (q.broker_mid - br_pos.entry_px) * self.cfg.price_ratio
        return pnl

    # ----- actions --------------------------------------------------------
    def _open(self, side: Side, q: Quote) -> None:
        size = self.cfg.position_size
        if side is Side.HL_SHORT_BROKER_LONG:
            log.info("OPEN HL_SHORT/BROKER_LONG (spread=%.2f bps)", q.spread_bps)
            self.hl.market_sell(size)
            self.broker.market_buy(size, ref_price=q.broker_mid)
        else:
            log.info("OPEN HL_LONG/BROKER_SHORT (spread=%.2f bps)", q.spread_bps)
            self.hl.market_buy(size)
            self.broker.market_sell(size, ref_price=q.broker_mid)

    def _close(self, q: Quote, reason: str) -> None:
        log.info("CLOSE both legs (%s, spread=%.2f bps)", reason, q.spread_bps)
        self.hl.close_position()
        self.broker.close_position()

    # ----- main step ------------------------------------------------------
    def step(self) -> Quote:
        q = self.quote()
        side = self.current_side()
        fund = f"{q.funding:.6f}" if q.funding is not None else "n/a"
        log.info(
            "hl=%.4f broker=%.4f(norm=%.4f) spread=%.2fbps funding=%s state=%s",
            q.hl_mid, q.broker_mid, q.broker_norm, q.spread_bps, fund, side.value,
        )

        if side is not Side.FLAT:
            pnl = self.unrealized_pnl(q)
            if pnl <= -abs(self.cfg.max_loss_usd):
                self._close(q, reason=f"max-loss hit (pnl={pnl:.2f})")
                return q
            if abs(q.spread_bps) <= self.cfg.exit_spread_bps:
                self._close(q, reason="spread reverted")
            return q

        # FLAT: look for an entry.
        if self.open_count() >= self.cfg.max_open_positions:
            return q
        if q.spread_bps >= self.cfg.entry_spread_bps:
            self._open(Side.HL_SHORT_BROKER_LONG, q)
        elif q.spread_bps <= -self.cfg.entry_spread_bps:
            self._open(Side.HL_LONG_BROKER_SHORT, q)
        return q
