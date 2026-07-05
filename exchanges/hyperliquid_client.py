"""Thin wrapper around the HyperLiquid SDK for the prices/funding/orders we need.

Imports of the SDK are done lazily so the rest of the project (and the paper
broker) can be used and unit-tested without the dependency installed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger("hl")


@dataclass
class HLPosition:
    symbol: str
    size: float  # signed: positive = long, negative = short
    entry_px: float


class HyperLiquidClient:
    def __init__(
        self,
        account_address: str,
        api_secret_key: str,
        symbol: str,
        network: str = "testnet",
        dry_run: bool = True,
        dex: str = "",
        timeout: float | None = 15.0,
    ) -> None:
        self.symbol = symbol
        self.network = network
        self.dry_run = dry_run
        self.account_address = account_address
        # Builder-deployed perp dex (HIP-3), e.g. "xyz" for the xyz:SP500 market.
        # Empty string = the default HyperLiquid perp dex.
        self.dex = dex

        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        base_url = (
            constants.MAINNET_API_URL if network == "mainnet" else constants.TESTNET_API_URL
        )
        self._base_url = base_url
        # A timeout avoids an indefinite hang if a meta/price request stalls.
        self.info = Info(base_url, skip_ws=True, timeout=timeout)
        if self.dex:
            # The SDK's name->coin map only covers the default dex; register the
            # builder coin so candle/lookup calls resolve it.
            self.info.name_to_coin.setdefault(symbol, symbol)

        self.exchange = None
        if not dry_run:
            from eth_account import Account
            from hyperliquid.exchange import Exchange

            wallet = Account.from_key(api_secret_key)
            self.exchange = Exchange(
                wallet, base_url, account_address=account_address or None
            )
        # Funding changes hourly; cache it briefly so the per-poll sampler
        # doesn't re-fetch the whole asset-context list every few seconds.
        self._funding: float | None = None
        self._funding_at = 0.0
        self._funding_ttl = 30.0

        log.info("HyperLiquid client ready (network=%s, dry_run=%s)", network, dry_run)

    # ----- market data ----------------------------------------------------
    def get_mid_price(self) -> float:
        mids = self.info.all_mids(self.dex)
        if self.symbol not in mids:
            raise KeyError(
                f"Symbol {self.symbol!r} not found on HyperLiquid. "
                f"Available examples: {list(mids)[:10]}"
            )
        return float(mids[self.symbol])

    def get_candles(self, interval: str, start_ms: int, end_ms: int) -> list[tuple[datetime, float]]:
        """Return [(utc_time, close_price)] candles for the symbol.

        ``interval`` is a HyperLiquid string like "1m", "5m", "1h".
        """
        raw = self.info.candles_snapshot(self.symbol, interval, int(start_ms), int(end_ms))
        out: list[tuple[datetime, float]] = []
        for c in raw or []:
            try:
                ts = datetime.fromtimestamp(int(c["t"]) / 1000, tz=timezone.utc)
                out.append((ts, float(c["c"])))
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def get_funding_rate(self) -> float | None:
        """Latest hourly funding rate for the symbol (positive => longs pay shorts).

        Works for both the default perp dex and builder-deployed (HIP-3) dexes
        like "xyz" by passing the dex to ``metaAndAssetCtxs``. Spot markets have
        no funding, so this returns ``None`` for them. Cached for a few seconds.
        """
        now = time.time()
        if self._funding_at and (now - self._funding_at) < self._funding_ttl:
            return self._funding

        value: float | None = None
        try:
            # metaAndAssetCtxs accepts a "dex" param; "" = the default perp dex.
            meta, ctxs = self.info.post(
                "/info", {"type": "metaAndAssetCtxs", "dex": self.dex}
            )
            for asset, ctx in zip(meta["universe"], ctxs):
                if asset["name"] == self.symbol:
                    funding = ctx.get("funding")
                    value = float(funding) if funding is not None else None
                    break
        except Exception as exc:  # pragma: no cover - network/shape variability
            log.warning("Could not fetch funding rate: %s", exc)
            value = self._funding  # keep last known value on transient errors

        self._funding = value
        self._funding_at = now
        return value

    # ----- account --------------------------------------------------------
    def get_position(self) -> HLPosition | None:
        if not self.account_address:
            return None
        state = self.info.user_state(self.account_address)
        for ap in state.get("assetPositions", []):
            pos = ap.get("position", {})
            if pos.get("coin") == self.symbol:
                size = float(pos.get("szi", 0.0))
                if size == 0:
                    return None
                return HLPosition(self.symbol, size, float(pos.get("entryPx") or 0.0))
        return None

    # ----- trading --------------------------------------------------------
    def market_buy(self, size: float) -> dict:
        return self._market(is_buy=True, size=size)

    def market_sell(self, size: float) -> dict:
        return self._market(is_buy=False, size=size)

    def close_position(self) -> dict:
        if self.dry_run or self.exchange is None:
            log.info("[DRY_RUN] HL close %s", self.symbol)
            return {"dry_run": True}
        return self.exchange.market_close(self.symbol)

    def _market(self, is_buy: bool, size: float) -> dict:
        side = "BUY" if is_buy else "SELL"
        if self.dry_run or self.exchange is None:
            log.info("[DRY_RUN] HL %s %s %s", side, size, self.symbol)
            return {"dry_run": True, "side": side, "size": size}
        result = self.exchange.market_open(self.symbol, is_buy, size)
        log.info("HL %s %s %s -> %s", side, size, self.symbol, result)
        return result
