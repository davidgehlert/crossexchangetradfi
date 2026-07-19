# S&P 500 Spread — HyperLiquid vs. capital.com

Monitors (and optionally arbitrage) the price **spread** of the S&P 500 between
the **HyperLiquid** `xyz:SP500` perp and a second venue. The default comparison
leg is **Yahoo Finance** (`^GSPC`, the cash index) — free, no account, runs on
macOS — with optional **MetaTrader 5** for a real broker feed.

The headline feature is a **web dashboard** that graphs the spread over the last
few days plus the current value, live in your browser. A delta-neutral trading
bot using the same spread is also included but is **off by default**.

> ⚠️ **Risk warning.** Algorithmic trading is risky and you can lose money. This
> code is provided "as is", with no warranty, and almost certainly contains
> bugs. Test thoroughly on **testnet / paper / a demo account** before risking
> real capital. Nothing here is intended investment advice.

## Quick start (just watch the spread)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python dashboard.py     # landing page at http://127.0.0.1:8000/ ; live charts at /app
```

The defaults compare HyperLiquid `xyz:SP500` against Yahoo `^GSPC` and never
place a trade.

## How it works is relatively easy

Each loop the bot:

1. Reads the **HyperLiquid** mid price for `HL_SYMBOL` (on `HL_DEX`).
2. Reads the **comparison-leg** mid price (Yahoo / MT5 / paper).
3. Normalizes the comparison price onto the HL scale with `PRICE_RATIO` and
   computes the spread in basis points.
4. **Entry:** if `|spread| ≥ ENTRY_SPREAD_BPS`, it shorts the rich venue and
   longs the cheap venue (equal size → ~delta-neutral).
5. **Exit:** once `|spread| ≤ EXIT_SPREAD_BPS`, it closes both legs. A hard
   `MAX_LOSS_USD` stop flattens everything if combined PnL drops too far.

```
spread_bps = (hl_mid - broker_mid * PRICE_RATIO) / mid_ref * 10_000
```

## Monitoring multiple symbols

The bot and dashboard can track several markets at once. The **primary** pair
comes from the `HL_SYMBOL` / `CAPITAL_EPIC` / `YAHOO_SYMBOL` / `MT5_SYMBOL` /
`PRICE_RATIO` env vars; **additional** pairs are listed in `EXTRA_SYMBOLS` as
comma-separated keys from the `PAIR_REGISTRY` in `config.py`.

```bash
EXTRA_SYMBOLS=jp225,brent,btc   # S&P 500 (primary) + Japan 225 + Brent + Bitcoin
EXTRA_SYMBOLS=                  # primary only
```

Built-in registry keys:

| Key | HyperLiquid | Capital epic | Yahoo | Notes |
| --- | --- | --- | --- | --- |
| `sp500` | `xyz:SP500` | `US500` | `^GSPC` | index CFD, market hours only |
| `jp225` | `xyz:JP225` | `J225` | `^N225` | index CFD, market hours only |
| `brent` | `xyz:BRENTOIL` | `OIL_BRENT` | `BZ=F` | structural basis (see below) |
| `btc` | `BTC` (perp) | `BTCUSD` | `BTC-USD` | 24/7 on both venues |
| `btcspot` | `@142` (UBTC/USDC spot) | `BTCUSD` | `BTC-USD` | HL spot leg; compare vs `btc` for basis |
| `ustech100` | `xyz:XYZ100` | `US100` | `^NDX` | Nasdaq-100 proxy, market hours only |
| `sndk` | `xyz:SNDK` | `SNDK` | `SNDK` | single stock, Capital **live** account only |

Add more by appending `SymbolPair` entries to `PAIR_REGISTRY`. Two notes:

* **`btc`** uses HL's *standard* `BTC` perp on the **default** dex (empty
  `hl_dex`), not a builder market — so HyperLiquid funding data is available for
  it, and both legs trade 24/7 (no weekend gaps). **`btcspot`** uses HL's
  `UBTC/USDC` spot market (index `@142`) against the same CFD; viewing both
  charts side by side shows the HyperLiquid perp-vs-spot basis.
* **`brent`**: HL's `xyz:BRENTOIL` tends to print near WTI levels while
  Capital's `OIL_BRENT` carries the usual Brent premium, so expect a structural
  basis — calibrate `price_ratio` (or swap the comparison epic) accordingly. The dashboard shows a
**Symbol** dropdown in the header, and each symbol keeps its own spread history
CSV (`state/spread_history.csv` for the primary, `state/spread_history_<key>.csv`
for the rest).

## Project layout

```
config.py                 # env-driven configuration + validation
exchanges/hyperliquid_client.py   # HyperLiquid prices, funding, orders
brokers/base.py           # broker interface
brokers/paper_broker.py   # simulated broker (any OS) for testing
brokers/mt5_broker.py     # real MetaTrader 5 broker (Windows only)
engine/arbitrage.py       # spread calc, entry/exit, risk logic
main.py                   # polling loop, logging, graceful shutdown
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then edit .env
```

`MetaTrader5` only installs/runs on **Windows** with the MT5 terminal present
(the recommended deployment is a Windows VPS). On macOS/Linux use `BROKER=paper`
to develop and test the logic.

## Running

```bash
python main.py
```

### Safe defaults

The shipped `.env.example` is intentionally conservative:

| Setting       | Default   | Meaning                                  |
| ------------- | --------- | ---------------------------------------- |
| `DRY_RUN`     | `true`    | log intended trades, place nothing       |
| `HL_NETWORK`  | `testnet` | HyperLiquid fake-money network           |
| `BROKER`      | `paper`   | simulated broker, no MT5 needed          |

To watch entries/exits fire locally, set `PAPER_SIMULATE=true` (optionally
`PAPER_SIMULATE_BPS` and `PAPER_SIMULATE_PERIOD_S`) — this overlays a synthetic
spread oscillation on the paper broker so you can see the state machine work.

## Visualizing the spread

The bot "sketches" the HyperLiquid vs. broker spread two ways:

**1. Live terminal sparkline** — every loop it logs a one-line view of the
recent spread (in bps), e.g.:

```
spread  +19.97bps |▄▅▆▇█▇▆▅▄▃▂▁▁▁▂▃▄▅▆▇█| min=-19.9 max=+20.0 [ENTRY]
```

Control the window width with `SKETCH_WIDTH` (default 60). `[ENTRY]`/`[exit]`
tags show when the spread is inside the entry/exit bands.

**2. Full chart (matplotlib)** — every observation is appended to
`state/spread_history.csv`. Render a PNG anytime:

```bash
python plot_spread.py            # -> state/spread.png
```

The chart shows both normalized price feeds on top and the spread (bps) with the
entry (red, dashed) and exit (green, dotted) threshold bands on the bottom.

**3. Live web dashboard (browser)** — a graph of the spread over the **last 24h**
plus the **current** spread, auto-refreshing in your browser:

```bash
python dashboard.py              # landing at http://127.0.0.1:8000/ ; dashboard at /app
```

It runs its own **read-only** sampler thread (it never places orders) that polls
both venues, records to the same `state/spread_history.csv`, and serves a live
Chart.js page. Useful env vars:

| Var | Default | Meaning |
| --- | --- | --- |
| `DASHBOARD_PORT` | `8000` | port to serve on |
| `DASHBOARD_HOST` | `127.0.0.1` | bind address (use `0.0.0.0` on a VPS) |
| `DASHBOARD_POLL` | `POLL_INTERVAL_SECONDS` | seconds between samples |
| `DASHBOARD_NO_OPEN` | `false` | set `true` to not auto-open the browser |
| `MAX_CHART_POINTS` | `1500` | downsample cap for the chart |
| `CANDLE_CACHE_TTL` | `20` | seconds to cache each candle-timeframe series |

**Timeframe selector.** A dropdown in the header lets you switch the resolution
of the spread graph:

| Choice | Source | Window |
| --- | --- | --- |
| Live (seconds) | the high-frequency sampler CSV | ~30 min |
| 1 minute | 1m candles from both venues | 6 h |
| 5 minutes | 5m candles | 3 days |
| 15 minutes | 15m candles | 7 days |
| 1 hour | 1h candles | 30 days |
| 1 day | daily candles | ~1 year |

"Live" shows the raw sampler points (as fine as `DASHBOARD_POLL`); the others
fetch aligned candles from HyperLiquid and the comparison venue on demand and
compute the spread at that resolution.

You can run `dashboard.py` on its own just to watch the spread, or alongside
`main.py` (both read/write the same CSV). With `BROKER=paper` the spread sits
near 0; use `PAPER_SIMULATE=true` to animate it locally, or `BROKER=mt5` on a
Windows VPS for the real cross-venue spread.

**Instant history (backfill).** On startup the dashboard backfills the last 24h
so the graph is populated immediately rather than starting empty. It pulls
HyperLiquid candles (and broker candles too when `BROKER=mt5`, via
`copy_rates_range`), aligns them by minute, and writes the computed spread. It
skips backfilling if recent data is already present. Tunables:

| Var | Default | Meaning |
| --- | --- | --- |
| `BACKFILL_INTERVAL_MIN` | `5` | candle size used for backfill (minutes) |
| `BACKFILL_SIMULATE_PERIOD_S` | `10800` | wave period for the paper-sim history |

With a paper broker there's no real broker history, so the backfilled spread is
~0 unless `PAPER_SIMULATE=true`.

### Going live (checklist)

1. Confirm the SPY/S&P instrument actually exists on **both** venues and set
   `HL_SYMBOL` / `MT5_SYMBOL` accordingly (see note below).
2. Calibrate `PRICE_RATIO` so the two feeds line up (SPY ETF ≈ 1/10 of SPX500).
3. Validate end-to-end on HyperLiquid **testnet** + an MT5 **demo** account.
4. Set `BROKER=mt5`, fill MT5 + HL credentials, run on a Windows VPS.
5. Only then set `HL_NETWORK=mainnet` and `DRY_RUN=false`, starting tiny.

## The S&P 500 on HyperLiquid (builder dex)

The S&P 500 perp is a **builder-deployed (HIP-3) market** on the `xyz` dex,
shown as `S&P500-USDC` in the UI. Its coin ticker is `xyz:SP500`, so the config
sets both `HL_SYMBOL=xyz:SP500` and `HL_DEX=xyz`. It tracks the cash index
roughly 1:1, so against Yahoo `^GSPC` you keep `PRICE_RATIO=1.0`. (The default
`all_mids()` list does **not** include builder markets — that's why `HL_DEX` is
needed.)

### Comparison-leg choices (`BROKER`)

| `BROKER` | Source | Account? | OS | Notes |
| --- | --- | --- | --- | --- |
| `yahoo` | Yahoo Finance `YAHOO_SYMBOL` | none | any | default; `^GSPC` index, `SPY` ETF, etc. |
| `mt5`   | MetaTrader 5 `MT5_SYMBOL` | broker | Windows | real broker feed (e.g. `US500`) |
| `paper` | mirrors HyperLiquid | none | any | spread ~0 unless `PAPER_SIMULATE` |

> Note: the S&P 500 cash index / ETF only updates during US market hours, while
> the HyperLiquid perp trades 24/7. Outside market hours the comparison price is
> stale, so the historical chart shows **gaps** rather than a fake flat spread.
> For a true 24/7 comparison you'd need another 24/7 venue.

## Security

- Never commit `.env` (it's git-ignored).
- Use a HyperLiquid **API wallet** key, not your main wallet's private key.
