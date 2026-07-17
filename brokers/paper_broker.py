"""Simulated broker for development/testing on any OS (no MT5 required).

It tracks a single net position in memory and reports the price it was given
via ``set_mark_price``. The engine feeds it the price-ratio-normalised HL price
as a stand-in feed when no real broker quote is available, but in practice you
should call ``set_mark_price`` with the broker feed each loop.
"""

from __future__ import annotations

import logging

from .base import Broker, BrokerPosition

log = logging.getLogger("paper")


class PaperBroker(Broker):
    def __init__(self, symbol: str, start_price: float = 0.0) -> None:
        self.symbol = symbol
        self._mark = start_price
        self._position: BrokerPosition | None = None

    def set_mark_price(self, price: float) -> None:
        self._mark = price

    def get_mid_price(self) -> float:
        return self._mark

    def get_position(self) -> BrokerPosition | None:
        return self._position

    def market_buy(self, size: float, ref_price: float | None = None) -> dict:
        return self._fill(+abs(size), ref_price)

    def market_sell(self, size: float, ref_price: float | None = None) -> dict:
        return self._fill(-abs(size), ref_price)

    def close_position(self) -> dict:
        if self._position is None:
            return {"closed": False}
        closed = self._position
        self._position = None
        log.info("[PAPER] close %s size=%s", self.symbol, closed.size)
        return {"closed": True, "size": closed.size}

    def _fill(self, signed_size: float, ref_price: float | None) -> dict:
        px = ref_price if ref_price is not None else self._mark
        if self._position is None:
            self._position = BrokerPosition(self.symbol, signed_size, px)
        else:
            new_size = self._position.size + signed_size
            self._position = BrokerPosition(self.symbol, new_size, px)
        log.info("[PAPER] fill %s size=%s @ %s", self.symbol, signed_size, px)
        return {"filled": signed_size, "price": px}


