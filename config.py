"""Central configuration loaded from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class SymbolPair:
    """One tradeable/monitorable market across both venues.

    ``key`` is a short slug used in URLs and history filenames; ``label`` is the
    human name. The remaining fields say how to reach this market on each venue
    and how to line the two price scales up (``price_ratio``).
    """

    key: str
    label: str
    hl_symbol: str
    hl_dex: str
    price_ratio: float
    capital_epic: str
    yahoo_symbol: str
    mt5_symbol: str
    # Optional per-pair override for which Capital.com environment to read from:
    # None -> use the global CAPITAL_DEMO setting; True -> demo; False -> live.
    # Some instruments (e.g. SanDisk) only exist on the live account.
    capital_demo: bool | None = None
    # Optional per-pair comparison-leg backend override (None -> use cfg.broker).
    # Lets one pair use e.g. free Yahoo data while the rest use Capital.com.
    broker: str | None = None
    # Optional Yahoo FX pair (e.g. "KRW=X") used to convert a foreign-currency
    # Yahoo quote into USD so it lines up with the USD-denominated HL perp.
    yahoo_fx: str | None = None


# Known markets that exist on the HyperLiquid "xyz" builder dex and have a
# matching comparison instrument. Add more here to monitor them.
PAIR_REGISTRY: dict[str, SymbolPair] = {
    "sp500": SymbolPair("sp500", "S&P 500", "xyz:SP500", "xyz", 1.0, "US500", "^GSPC", "US500"),
    "jp225": SymbolPair("jp225", "Japan 225", "xyz:JP225", "xyz", 1.0, "J225", "^N225", "JP225"),
    # Brent: HL's xyz:BRENTOIL prints near WTI levels and Capital's OIL_BRENT
    # carries the usual Brent premium, so expect a structural basis here —
    # calibrate price_ratio (or the comparison epic) once CFD markets are open.
    "brent": SymbolPair("brent", "Brent Oil", "xyz:BRENTOIL", "xyz", 1.0, "OIL_BRENT", "BZ=F", "UKOIL"),
    # Bitcoin: HL's standard BTC perp (default dex, so funding data works) vs
    # Capital's BTCUSD CFD. Both trade 24/7, so this is a true round-the-clock
    # comparison (no weekend/off-hours gaps like the index/oil pairs).
    "btc": SymbolPair("btc", "Bitcoin (HL perp)", "BTC", "", 1.0, "BTCUSD", "BTC-USD", "BTCUSD"),
    # Bitcoin spot: HL's UBTC/USDC spot market (spot index "@142", default dex)
    # vs the same Capital BTCUSD CFD. Compare this chart against "btc" to see the
    # HyperLiquid perp-vs-spot basis. Spot has no funding (perp-only concept).
    "btcspot": SymbolPair("btcspot", "Bitcoin (HL spot)", "@142", "", 1.0, "BTCUSD", "BTC-USD", "BTCUSD"),
    # US Tech 100 (Nasdaq-100 proxy): HL's xyz:XYZ100 vs Capital's US100. Both
    # ~29,000 scale, so price_ratio 1.0.
    "ustech100": SymbolPair("ustech100", "US Tech 100", "xyz:XYZ100", "xyz", 1.0, "US100", "^NDX", "USTEC"),
    # SanDisk: HL's xyz:SNDK perp vs Capital's SNDK ("SanDisk Corp") stock. Both
    # print on the same ~1,800 scale, so price_ratio 1.0. The SNDK epic only
    # exists on Capital's LIVE account, so this pair forces capital_demo=False.
    "sndk": SymbolPair("sndk", "SanDisk", "xyz:SNDK", "xyz", 1.0, "SNDK", "SNDK", "SNDK", capital_demo=False),
    # SK Hynix: HL's xyz:SKHX perp (USD) vs the Korea listing 000660.KS (KRW) from
    # free Yahoo data (no account needed). Capital.com lists no SK Hynix, so this
    # pair uses the Yahoo backend and converts KRW->USD via the KRW=X FX pair so
    # the two legs share a scale (price_ratio 1.0). Note: KRX only trades during
    # Korean hours, so the Yahoo quote is a stale last-close outside that window.
    "skhynix": SymbolPair(
        "skhynix", "SK Hynix", "xyz:SKHX", "xyz", 1.0, "", "000660.KS", "000660.KS",
        broker="yahoo", yahoo_fx="KRW=X",
    ),
}


@dataclass(frozen=True)
class Config:
    # Run mode
    dry_run: bool
    hl_network: str  # "testnet" | "mainnet"
    broker: str  # "paper" | "mt5"

    # HyperLiquid
    hl_account_address: str
    hl_api_secret_key: str
    hl_symbol: str
    hl_dex: str

    # MetaTrader 5
    mt5_login: str
    mt5_password: str
    mt5_server: str
    mt5_terminal_path: str
    mt5_symbol: str

    # Yahoo data source (comparison leg)
    yahoo_symbol: str

    # Capital.com data source (comparison leg)
    capital_api_key: str
    capital_identifier: str
    capital_password: str
    capital_demo: bool
    capital_epic: str

    # Pricing
    price_ratio: float

    # Strategy
    entry_spread_bps: float
    exit_spread_bps: float
    position_size: float
    max_open_positions: int

    # Risk / loop
    poll_interval_seconds: float
    max_loss_usd: float

    # Markets to monitor/trade. The first is the "primary" pair (built from the
    # legacy single-symbol env vars for backward compatibility); the rest come
    # from EXTRA_SYMBOLS via PAIR_REGISTRY.
    pairs: tuple[SymbolPair, ...]

    @staticmethod
    def _build_pairs(
        hl_symbol: str, hl_dex: str, price_ratio: float,
        capital_epic: str, yahoo_symbol: str, mt5_symbol: str,
    ) -> "tuple[SymbolPair, ...]":
        """Primary pair from the legacy env vars, plus any EXTRA_SYMBOLS."""
        # Borrow a nice key/label if the primary HL symbol is a known market.
        known = next((p for p in PAIR_REGISTRY.values() if p.hl_symbol == hl_symbol), None)
        if known is not None:
            key, label = known.key, known.label
        else:
            ticker = hl_symbol.split(":")[-1]
            key, label = ticker.lower(), ticker
        primary = SymbolPair(
            key=key, label=label, hl_symbol=hl_symbol, hl_dex=hl_dex,
            price_ratio=price_ratio, capital_epic=capital_epic,
            yahoo_symbol=yahoo_symbol, mt5_symbol=mt5_symbol,
        )

        pairs = [primary]
        seen = {primary.key}
        raw = os.getenv("EXTRA_SYMBOLS", "jp225")
        for k in (s.strip().lower() for s in raw.split(",")):
            if not k or k in seen:
                continue
            extra = PAIR_REGISTRY.get(k)
            if extra is None:
                continue
            pairs.append(extra)
            seen.add(k)
        return tuple(pairs)

    @classmethod
    def load(cls) -> "Config":
        hl_symbol = os.getenv("HL_SYMBOL", "xyz:SP500").strip()
        hl_dex = os.getenv("HL_DEX", "").strip()
        price_ratio = _get_float("PRICE_RATIO", 1.0)
        capital_epic = os.getenv("CAPITAL_EPIC", "US500").strip()
        yahoo_symbol = os.getenv("YAHOO_SYMBOL", "^GSPC").strip()
        mt5_symbol = os.getenv("MT5_SYMBOL", "SPY").strip()
        cfg = cls(
            dry_run=_get_bool("DRY_RUN", True),
            hl_network=os.getenv("HL_NETWORK", "testnet").strip().lower(),
            broker=os.getenv("BROKER", "paper").strip().lower(),
            hl_account_address=os.getenv("HL_ACCOUNT_ADDRESS", "").strip(),
            hl_api_secret_key=os.getenv("HL_API_SECRET_KEY", "").strip(),
            hl_symbol=hl_symbol,
            hl_dex=hl_dex,
            mt5_login=os.getenv("MT5_LOGIN", "").strip(),
            mt5_password=os.getenv("MT5_PASSWORD", "").strip(),
            mt5_server=os.getenv("MT5_SERVER", "").strip(),
            mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH", "").strip(),
            mt5_symbol=mt5_symbol,
            yahoo_symbol=yahoo_symbol,
            capital_api_key=os.getenv("CAPITAL_API_KEY", "").strip(),
            capital_identifier=os.getenv("CAPITAL_IDENTIFIER", "").strip(),
            capital_password=os.getenv("CAPITAL_PASSWORD", "").strip(),
            capital_demo=_get_bool("CAPITAL_DEMO", True),
            capital_epic=capital_epic,
            price_ratio=price_ratio,
            entry_spread_bps=_get_float("ENTRY_SPREAD_BPS", 15.0),
            exit_spread_bps=_get_float("EXIT_SPREAD_BPS", 3.0),
            position_size=_get_float("POSITION_SIZE", 1.0),
            max_open_positions=_get_int("MAX_OPEN_POSITIONS", 1),
            poll_interval_seconds=_get_float("POLL_INTERVAL_SECONDS", 5.0),
            max_loss_usd=_get_float("MAX_LOSS_USD", 100.0),
            pairs=cls._build_pairs(
                hl_symbol, hl_dex, price_ratio, capital_epic, yahoo_symbol, mt5_symbol
            ),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.hl_network not in {"testnet", "mainnet"}:
            raise ValueError(f"HL_NETWORK must be testnet|mainnet, got {self.hl_network!r}")
        if self.broker not in {"paper", "mt5", "yahoo", "capital"}:
            raise ValueError(f"BROKER must be paper|mt5|yahoo|capital, got {self.broker!r}")
        if self.broker == "capital" and not (
            self.capital_api_key and self.capital_identifier and self.capital_password
        ):
            raise ValueError(
                "BROKER=capital requires CAPITAL_API_KEY, CAPITAL_IDENTIFIER and "
                "CAPITAL_PASSWORD in your .env"
            )
        if self.exit_spread_bps >= self.entry_spread_bps:
            raise ValueError("EXIT_SPREAD_BPS should be smaller than ENTRY_SPREAD_BPS")
        if self.position_size <= 0:
            raise ValueError("POSITION_SIZE must be positive")
        # Credentials are only strictly required when actually trading live.
        if not self.dry_run:
            if not self.hl_account_address or not self.hl_api_secret_key:
                raise ValueError("HL credentials required when DRY_RUN=false")
            if self.broker == "mt5" and not (self.mt5_login and self.mt5_password and self.mt5_server):
                raise ValueError("MT5 credentials required when BROKER=mt5 and DRY_RUN=false")
