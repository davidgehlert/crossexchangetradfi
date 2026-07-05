from .base import Broker, BrokerPosition
from .paper_broker import PaperBroker


def build_broker_for_pair(cfg, pair) -> Broker:
    """Factory: build the comparison-leg broker for a specific symbol pair.

    Uses the venue selected by ``cfg.broker`` (or the pair's own ``broker``
    override) with the per-pair instrument (``pair.capital_epic`` /
    ``pair.yahoo_symbol`` / ``pair.mt5_symbol``).
    """
    venue = pair.broker or cfg.broker
    if venue == "mt5":
        from .mt5_broker import MT5Broker

        return MT5Broker(
            login=cfg.mt5_login,
            password=cfg.mt5_password,
            server=cfg.mt5_server,
            symbol=pair.mt5_symbol,
            terminal_path=cfg.mt5_terminal_path,
            dry_run=cfg.dry_run,
        )
    if venue == "yahoo":
        from .yahoo_source import YahooSource

        return YahooSource(symbol=pair.yahoo_symbol, fx_symbol=pair.yahoo_fx)
    if venue == "capital":
        from .capital_broker import CapitalBroker

        # A pair may pin itself to demo/live (some epics only exist on live);
        # fall back to the global setting when it doesn't care.
        demo = cfg.capital_demo if pair.capital_demo is None else pair.capital_demo
        return CapitalBroker(
            api_key=cfg.capital_api_key,
            identifier=cfg.capital_identifier,
            password=cfg.capital_password,
            epic=pair.capital_epic,
            demo=demo,
        )
    return PaperBroker(symbol=pair.mt5_symbol)


def build_broker(cfg) -> Broker:
    """Backward-compatible factory for the primary (first) pair."""
    return build_broker_for_pair(cfg, cfg.pairs[0])


__all__ = [
    "Broker",
    "BrokerPosition",
    "PaperBroker",
    "build_broker",
    "build_broker_for_pair",
]
