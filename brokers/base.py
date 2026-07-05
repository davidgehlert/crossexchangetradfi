"""Broker interface so the engine is agnostic to MT5 vs. paper trading."""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class BrokerPosition:
    symbol: str
    size: float  # signed: positive = long, negative = short
    entry_px: float


class Broker(abc.ABC):
    """Minimal trading interface used by the arbitrage engine."""

    symbol: str

    @abc.abstractmethod
    def get_mid_price(self) -> float:
        ...

    @abc.abstractmethod
    def get_position(self) -> BrokerPosition | None:
        ...

    @abc.abstractmethod
    def market_buy(self, size: float, ref_price: float | None = None) -> dict:
        ...

    @abc.abstractmethod
    def market_sell(self, size: float, ref_price: float | None = None) -> dict:
        ...

    @abc.abstractmethod
    def close_position(self) -> dict:
        ...

    def get_history(self, start, end, interval_minutes: int = 5):
        """Optional: return [(utc_time, price)] history, or None if unsupported."""
        return None

    def shutdown(self) -> None:  # optional override
        return None
