"""Offline logic tests for the arbitrage engine (no network / SDK required)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brokers.paper_broker import PaperBroker
from engine.arbitrage import ArbitrageEngine, Side


class FakeCfg:
    price_ratio = 1.0
    entry_spread_bps = 15.0
    exit_spread_bps = 3.0
    position_size = 1.0
    max_open_positions = 1
    max_loss_usd = 100.0


class FakeHL:
    """In-memory stand-in for HyperLiquidClient."""

    def __init__(self):
        self.mid = 100.0
        self.position = None  # (size, entry_px)

    def get_mid_price(self):
        return self.mid

    def get_funding_rate(self):
        return 0.0001

    def get_position(self):
        if self.position is None:
            return None
        from exchanges.hyperliquid_client import HLPosition

        size, px = self.position
        return HLPosition("SPY", size, px)

    def market_buy(self, size):
        self.position = (size, self.mid)

    def market_sell(self, size):
        self.position = (-size, self.mid)

    def close_position(self):
        self.position = None


def make_engine():
    hl = FakeHL()
    broker = PaperBroker("SPY", start_price=100.0)
    return ArbitrageEngine(FakeCfg(), hl, broker), hl, broker


def test_no_trade_when_spread_small():
    eng, hl, broker = make_engine()
    hl.mid = 100.0
    broker.set_mark_price(100.0)  # spread ~ 0 bps
    eng.step()
    assert eng.current_side() is Side.FLAT


def test_enter_when_hl_rich():
    eng, hl, broker = make_engine()
    hl.mid = 100.5  # ~50 bps rich vs broker 100.0
    broker.set_mark_price(100.0)
    eng.step()
    assert eng.current_side() is Side.HL_SHORT_BROKER_LONG
    assert hl.position[0] < 0  # short HL
    assert broker.get_position().size > 0  # long broker


def test_enter_when_hl_cheap():
    eng, hl, broker = make_engine()
    hl.mid = 99.5  # ~50 bps cheap vs broker 100.0
    broker.set_mark_price(100.0)
    eng.step()
    assert eng.current_side() is Side.HL_LONG_BROKER_SHORT
    assert hl.position[0] > 0  # long HL
    assert broker.get_position().size < 0  # short broker


def test_exit_when_spread_reverts():
    eng, hl, broker = make_engine()
    hl.mid = 100.5
    broker.set_mark_price(100.0)
    eng.step()  # open
    assert eng.current_side() is not Side.FLAT
    hl.mid = 100.0  # spread back to ~0
    broker.set_mark_price(100.0)
    eng.step()  # should close
    assert eng.current_side() is Side.FLAT


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"\n{len(tests)} tests passed.")
