"""Read-only market-data source for the comparison leg via Yahoo Finance.

This lets the dashboard compare the HyperLiquid S&P 500 perp against a real
S&P 500 quote (default ``^GSPC``, the cash index) with **no broker account and
no Windows** — it just reads Yahoo's public chart API.

It implements the ``Broker`` interface for prices/history only. The trading
methods raise, because this source is for monitoring the spread, not trading.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .base import Broker, BrokerPosition

log = logging.getLogger("yahoo")

_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


class YahooSource(Broker):
    def __init__(
        self,
        symbol: str = "^GSPC",
        price_cache_ttl: float = 10.0,
        fx_symbol: str | None = None,
    ) -> None:
        """Read prices for ``symbol`` from Yahoo.

        ``fx_symbol`` optionally converts a foreign-currency quote into the
        currency implied by that FX pair. Yahoo FX pairs like ``KRW=X`` quote
        *units of the foreign currency per 1 USD* (e.g. 1530 KRW = 1 USD), so a
        local price is turned into USD by dividing by the FX rate. This lets us
        compare, say, SK Hynix (priced in KRW on ``000660.KS``) against a USD HL
        perp on a like-for-like basis instead of drowning the spread in FX drift.
        """
        self.symbol = symbol
        self._ttl = price_cache_ttl
        self._cached_price: float | None = None
        self._cached_at = 0.0
        self._fx = YahooSource(fx_symbol, price_cache_ttl) if fx_symbol else None

    # ----- internal -------------------------------------------------------
    def _fetch(self, range_: str, interval: str) -> dict:
        url = _BASE + urllib.parse.quote(self.symbol) + f"?range={range_}&interval={interval}"
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        result = data.get("chart", {}).get("result")
        if not result:
            err = data.get("chart", {}).get("error")
            raise RuntimeError(f"Yahoo returned no data for {self.symbol!r}: {err}")
        return result[0]

    # ----- market data ----------------------------------------------------
    def get_mid_price(self) -> float:
        now = time.time()
        if self._cached_price is not None and (now - self._cached_at) < self._ttl:
            return self._cached_price
        r = self._fetch("1d", "1m")
        price = r.get("meta", {}).get("regularMarketPrice")
        if price is None:  # fall back to last non-null close
            closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            price = next((c for c in reversed(closes) if c is not None), None)
        if price is None:
            raise RuntimeError(f"No price available for {self.symbol!r}")
        price = float(price)
        if self._fx is not None:
            fx = self._fx.get_mid_price()
            if fx:
                price /= fx
        self._cached_price = price
        self._cached_at = now
        return self._cached_price

    def get_history(self, start, end, interval_minutes: int = 5):
        # Pick a Yahoo range big enough to cover [start, end]; we filter after.
        span_hours = (end - start).total_seconds() / 3600.0
        range_ = "1d" if span_hours <= 24 else ("5d" if span_hours <= 5 * 24 else "1mo")
        interval = f"{interval_minutes}m"
        r = self._fetch(range_, interval)
        ts = r.get("timestamp") or []
        closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", [])

        # Build an FX lookup (foreign-per-USD) so each bar can be converted using
        # a rate near its own timestamp, falling back to the last known rate.
        fx_points: list[tuple[float, float]] = []
        fx_live: float | None = None
        if self._fx is not None:
            try:
                fx_points = [(dt.timestamp(), rate) for dt, rate in
                             self._fx.get_history(start, end, interval_minutes)]
                fx_points.sort()
            except Exception:  # pragma: no cover - FX history best-effort
                fx_points = []
            try:
                fx_live = self._fx.get_mid_price()
            except Exception:  # pragma: no cover
                fx_live = None

        def fx_at(epoch: float) -> float | None:
            if self._fx is None:
                return 1.0
            rate = fx_live
            for t, r_ in fx_points:  # last point at or before this bar
                if t <= epoch:
                    rate = r_
                else:
                    break
            return rate

        out = []
        for t, c in zip(ts, closes):
            if c is None:
                continue
            dt = datetime.fromtimestamp(int(t), tz=timezone.utc)
            if not (start <= dt <= end):
                continue
            price = float(c)
            rate = fx_at(dt.timestamp())
            if rate:
                price /= rate
            out.append((dt, price))
        return out

    # ----- account / trading (unsupported: data-only) --------------------
    def get_position(self) -> BrokerPosition | None:
        return None

    def _readonly(self, *_a, **_k):
        raise NotImplementedError(
            "YahooSource is a read-only data feed (monitoring only); it cannot trade."
        )

    market_buy = _readonly
    market_sell = _readonly
    close_position = _readonly
