"""MetaTrader 5 broker implementation.

IMPORTANT: the ``MetaTrader5`` Python package only runs on Windows with the
MT5 terminal installed (the tutorial deploys this on a Windows VPS). The import
is lazy so the rest of the project still works on macOS/Linux in paper mode.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .base import Broker, BrokerPosition

log = logging.getLogger("mt5")


class MT5Broker(Broker):
    def __init__(
        self,
        login: str,
        password: str,
        server: str,
        symbol: str,
        terminal_path: str = "",
        dry_run: bool = True,
    ) -> None:
        self.symbol = symbol
        self.dry_run = dry_run

        import MetaTrader5 as mt5  # type: ignore

        self.mt5 = mt5
        init_kwargs = {}
        if terminal_path:
            init_kwargs["path"] = terminal_path
        if login and password and server:
            init_kwargs.update(login=int(login), password=password, server=server)

        if not mt5.initialize(**init_kwargs):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 could not select symbol {symbol!r}: {mt5.last_error()}")
        log.info("MT5 broker ready (symbol=%s, dry_run=%s)", symbol, dry_run)

    def get_mid_price(self) -> float:
        tick = self.mt5.symbol_info_tick(self.symbol)
        if tick is None:
            raise RuntimeError(f"No tick for {self.symbol}: {self.mt5.last_error()}")
        return (tick.bid + tick.ask) / 2.0

    def get_position(self) -> BrokerPosition | None:
        positions = self.mt5.positions_get(symbol=self.symbol)
        if not positions:
            return None
        net = 0.0
        weighted_px = 0.0
        for p in positions:
            signed = p.volume if p.type == self.mt5.POSITION_TYPE_BUY else -p.volume
            net += signed
            weighted_px += signed * p.price_open
        if net == 0:
            return None
        return BrokerPosition(self.symbol, net, weighted_px / net)

    def market_buy(self, size: float, ref_price: float | None = None) -> dict:
        return self._order(self.mt5.ORDER_TYPE_BUY, abs(size))

    def market_sell(self, size: float, ref_price: float | None = None) -> dict:
        return self._order(self.mt5.ORDER_TYPE_SELL, abs(size))

    def close_position(self) -> dict:
        pos = self.get_position()
        if pos is None:
            return {"closed": False}
        # Closing a netting position = send the opposite market order.
        if pos.size > 0:
            return self._order(self.mt5.ORDER_TYPE_SELL, abs(pos.size))
        return self._order(self.mt5.ORDER_TYPE_BUY, abs(pos.size))

    def _order(self, order_type, volume: float) -> dict:
        side = "BUY" if order_type == self.mt5.ORDER_TYPE_BUY else "SELL"
        if self.dry_run:
            log.info("[DRY_RUN] MT5 %s %s %s", side, volume, self.symbol)
            return {"dry_run": True, "side": side, "volume": volume}

        tick = self.mt5.symbol_info_tick(self.symbol)
        price = tick.ask if order_type == self.mt5.ORDER_TYPE_BUY else tick.bid
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": float(volume),
            "type": order_type,
            "price": price,
            "deviation": 20,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
            "type_time": self.mt5.ORDER_TIME_GTC,
            "comment": "spy-arb",
        }
        result = self.mt5.order_send(request)
        log.info("MT5 %s %s %s -> retcode=%s", side, volume, self.symbol, getattr(result, "retcode", None))
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"MT5 order failed: {getattr(result, 'comment', self.mt5.last_error())}")
        return {"retcode": result.retcode, "order": result.order, "price": result.price}

    def get_history(self, start, end, interval_minutes: int = 5):
        timeframes = {
            1: self.mt5.TIMEFRAME_M1,
            5: self.mt5.TIMEFRAME_M5,
            15: self.mt5.TIMEFRAME_M15,
            30: self.mt5.TIMEFRAME_M30,
            60: self.mt5.TIMEFRAME_H1,
        }
        tf = timeframes.get(interval_minutes, self.mt5.TIMEFRAME_M5)
        rates = self.mt5.copy_rates_range(self.symbol, tf, start, end)
        if rates is None:
            log.warning("MT5 copy_rates_range returned None: %s", self.mt5.last_error())
            return None
        out = []
        for r in rates:
            ts = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc)
            out.append((ts, float(r["close"])))
        return out

    def shutdown(self) -> None:
        try:
            self.mt5.shutdown()
        except Exception:  # pragma: no cover
            pass
