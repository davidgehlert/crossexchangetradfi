"""Web dashboard: live + last-24h chart of the HyperLiquid vs. broker spread.

Run it and open the printed URL in your browser:

    python dashboard.py

It does two things:
  1. A background thread samples both venues every few seconds (READ ONLY — it
     never places orders) and appends to state/spread_history.csv.
  2. A Flask server renders a page with the current spread and an
     auto-updating chart of the spread over the last 24 hours.

Notes
-----
* With BROKER=paper the "broker" price mirrors HyperLiquid, so the spread sits
  near 0. Set PAPER_SIMULATE=true to animate a synthetic spread locally, or set
  BROKER=mt5 on a Windows VPS for the real cross-venue spread.
* The sampler is independent of main.py; you can run the dashboard on its own
  just to watch the spread, or run both side by side.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request

from brokers import build_broker_for_pair
from brokers.paper_broker import PaperBroker
from config import Config, SymbolPair
from engine.arbitrage import ArbitrageEngine
from engine.spread_monitor import (
    SpreadRecorder,
    downsample,
    history_path,
    load_history,
    write_history,
)
from exchanges.hyperliquid_client import HyperLiquidClient

log = logging.getLogger("dashboard")


def _window_hours() -> float:
    return float(os.getenv("DASHBOARD_WINDOW_HOURS", "72"))


# Selectable timeframes for the dashboard. "live" reads the high-frequency
# sampler CSV; the others fetch aligned candles from both venues on demand.
TIMEFRAMES = [
    {"key": "live", "label": "Live (seconds)", "source": "csv",    "window_h": 0.5,  "fmt": "hms"},
    {"key": "1m",   "label": "1 minute",       "source": "candle", "hl": "1m",  "min": 1,    "window_h": 6,    "fmt": "hm"},
    {"key": "5m",   "label": "5 minutes",      "source": "candle", "hl": "5m",  "min": 5,    "window_h": 72,   "fmt": "dhm"},
    {"key": "15m",  "label": "15 minutes",     "source": "candle", "hl": "15m", "min": 15,   "window_h": 168,  "fmt": "dhm"},
    {"key": "1h",   "label": "1 hour",         "source": "candle", "hl": "1h",  "min": 60,   "window_h": 720,  "fmt": "dhm"},
    {"key": "1d",   "label": "1 day",          "source": "candle", "hl": "1d",  "min": 1440, "window_h": 8760, "fmt": "date"},
]
TF_BY_KEY = {t["key"]: t for t in TIMEFRAMES}
DEFAULT_TF = "5m"

_candle_cache: dict = {}
_candle_cache_lock = threading.Lock()


def _fmt_label(ts: datetime, fmt: str) -> str:
    ts = ts.astimezone(timezone.utc)
    if fmt == "hms":
        return ts.strftime("%H:%M:%S")
    if fmt == "hm":
        return ts.strftime("%H:%M")
    if fmt == "date":
        return ts.strftime("%Y-%m-%d")
    return ts.strftime("%m-%d %H:%M")


def _candle_series(pair: SymbolPair, hl: HyperLiquidClient, broker, tf: str) -> list[dict]:
    """Compute the spread series for a candle timeframe from both venues (cached)."""
    preset = TF_BY_KEY[tf]
    ttl = float(os.getenv("CANDLE_CACHE_TTL", "20"))
    now_t = time.time()
    cache_key = (pair.key, tf)
    with _candle_cache_lock:
        cached = _candle_cache.get(cache_key)
        if cached and now_t - cached[0] < ttl:
            return cached[1]

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=preset["window_h"])
    candles = hl.get_candles(preset["hl"], int(start.timestamp() * 1000), int(end.timestamp() * 1000))
    bhist = broker.get_history(start, end, preset["min"])
    bucket_s = preset["min"] * 60
    bmap = {int(ts.timestamp() // bucket_s): price for ts, price in (bhist or [])}

    rows = []
    for ts, hl_price in candles:
        bp = bmap.get(int(ts.timestamp() // bucket_s))
        if bp is None:
            continue  # need both venues to form a spread
        broker_norm = bp * pair.price_ratio
        mid = (hl_price + broker_norm) / 2.0
        spread = ((hl_price - broker_norm) / mid * 10_000) if mid else 0.0
        rows.append({"ts": ts, "hl": hl_price, "broker": broker_norm, "spread": spread})
    rows = downsample(rows, int(os.getenv("MAX_CHART_POINTS", "1500")))

    with _candle_cache_lock:
        _candle_cache[cache_key] = (now_t, rows)
    return rows


app = Flask(__name__)




class PairRuntime:
    """Everything the dashboard needs to sample/serve one symbol pair."""

    def __init__(self, cfg: Config, pair: SymbolPair, primary: bool) -> None:
        self.pair = pair
        self.path = history_path(pair.key, primary=primary)
        # One read-only HL client per pair (the dashboard never trades), shared
        # between the sampler thread and web candle requests. Each HL client
        # fetches meta on construction, so we keep the count low (one per pair).
        self.hl = HyperLiquidClient(
            account_address=cfg.hl_account_address, api_secret_key=cfg.hl_api_secret_key,
            symbol=pair.hl_symbol, network=cfg.hl_network, dry_run=True, dex=pair.hl_dex,
        )
        self.broker = build_broker_for_pair(cfg, pair)
        self.engine = ArbitrageEngine(cfg, self.hl, self.broker)
        self.recorder = SpreadRecorder(path=self.path)
        self.latest = {
            "ts": None, "hl_mid": None, "broker_mid": None, "broker_norm": None,
            "spread_bps": None, "funding": None, "ok": False, "error": None,
        }


# Latest snapshots shared between the sampler thread and the web routes,
# keyed by symbol pair key.
_lock = threading.Lock()
_stop = threading.Event()


def _backfill(cfg: Config, rt: PairRuntime) -> None:
    """Populate the last 24h of one symbol's spread history so the graph isn't empty.

    Pulls HyperLiquid candles and, if the broker supports it (MT5), broker
    candles too, aligns them by minute and writes the computed spread to the
    pair's CSV. Skips if recent data already exists. With a paper broker (no
    history), the broker leg mirrors HL (spread ~0) unless PAPER_SIMULATE.
    """
    pair = rt.pair
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=_window_hours())

    existing = load_history(rt.path)
    recent = [r for r in existing if r["ts"] >= start]
    if len(recent) >= 2:
        log.info("[%s] backfill skipped (%d recent points present)", pair.key, len(recent))
        return

    interval_min = int(os.getenv("BACKFILL_INTERVAL_MIN", "5"))
    try:
        candles = rt.hl.get_candles(
            f"{interval_min}m", int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        )
    except Exception as exc:
        log.warning("[%s] HL candle backfill failed: %s", pair.key, exc)
        return
    if not candles:
        log.info("[%s] backfill: no HL candles returned", pair.key)
        return

    bmap: dict = {}
    try:
        bhist = rt.broker.get_history(start, end, interval_min)
        for ts, price in bhist or []:
            bmap[ts.replace(second=0, microsecond=0)] = price
    except Exception as exc:
        log.warning("[%s] broker history backfill failed: %s", pair.key, exc)
    # If the comparison source has real history (e.g. Yahoo/MT5), only emit
    # points where both venues have data so off-hours show honest gaps rather
    # than a fake 0 spread. With no broker history (paper) we mirror/simulate.
    has_broker_history = bool(bmap)

    simulate = os.getenv("PAPER_SIMULATE", "false").strip().lower() in {"1", "true", "yes"}
    amp = float(os.getenv("PAPER_SIMULATE_BPS", "25"))
    # Use a multi-hour period for the historical wave so it doesn't alias at the
    # candle spacing (the live loop uses the shorter PAPER_SIMULATE_PERIOD_S).
    period = float(os.getenv("BACKFILL_SIMULATE_PERIOD_S", "10800"))

    rows = []
    for ts, hl_price in candles:
        broker_price = bmap.get(ts.replace(second=0, microsecond=0))
        if broker_price is None:
            if has_broker_history:
                continue  # real feed but no data this minute -> leave a gap
            broker_price = hl_price / pair.price_ratio if pair.price_ratio else hl_price
            if simulate:
                broker_price *= 1 + (amp / 10_000) * math.sin(ts.timestamp() / period * 2 * math.pi)
        broker_norm = broker_price * pair.price_ratio
        mid = (hl_price + broker_norm) / 2.0
        spread = ((hl_price - broker_norm) / mid * 10_000) if mid else 0.0
        rows.append(
            {
                "ts": ts, "hl_mid": hl_price, "broker_mid": broker_price,
                "broker_norm": broker_norm, "spread_bps": spread, "funding": None,
            }
        )

    # Merge: existing rows win over backfilled ones at the same minute.
    merged = {r["ts"].replace(second=0, microsecond=0): r for r in rows}
    for r in existing:
        merged[r["ts"].replace(second=0, microsecond=0)] = r
    write_history(list(merged.values()), rt.path)
    log.info("[%s] backfilled %d points over the last %.0fh", pair.key, len(rows), _window_hours())


def _sampler(cfg: Config, runtimes: list[PairRuntime]) -> None:
    """Read-only loop: sample both venues for every pair and record the spread."""
    poll = float(os.getenv("DASHBOARD_POLL", str(cfg.poll_interval_seconds)))
    t0 = time.time()
    while not _stop.is_set():
        for rt in runtimes:
            try:
                if isinstance(rt.broker, PaperBroker):
                    hl_mid = rt.hl.get_mid_price()
                    base = hl_mid / rt.pair.price_ratio if rt.pair.price_ratio else hl_mid
                    if os.getenv("PAPER_SIMULATE", "false").strip().lower() in {"1", "true", "yes"}:
                        amp = float(os.getenv("PAPER_SIMULATE_BPS", "25"))
                        period = float(os.getenv("PAPER_SIMULATE_PERIOD_S", "60"))
                        base *= 1 + (amp / 10_000) * math.sin((time.time() - t0) / period * 2 * math.pi)
                    rt.broker.set_mark_price(base)

                q = rt.engine.quote()  # quote() only reads prices; it never trades.
                rt.recorder.record(q)
                with _lock:
                    rt.latest.update(
                        ts=datetime.now(timezone.utc).isoformat(),
                        hl_mid=q.hl_mid, broker_mid=q.broker_mid,
                        broker_norm=q.broker_norm, spread_bps=q.spread_bps,
                        funding=q.funding, ok=True, error=None,
                    )
            except Exception as exc:
                log.warning("[%s] sample failed: %s", rt.pair.key, exc)
                with _lock:
                    rt.latest.update(ok=False, error=str(exc))
        _stop.wait(poll)


@app.route("/api/spread")
def api_spread():
    """Return the current snapshot + the spread series for the requested timeframe."""
    cfg = app.config["CFG"]
    runtimes: dict[str, PairRuntime] = app.config["RUNTIMES"]
    order: list[str] = app.config["ORDER"]

    sym = request.args.get("symbol", order[0])
    if sym not in runtimes:
        sym = order[0]
    rt = runtimes[sym]

    tf = request.args.get("tf", DEFAULT_TF)
    if tf not in TF_BY_KEY:
        tf = DEFAULT_TF
    preset = TF_BY_KEY[tf]

    if preset["source"] == "csv":
        since = datetime.now(timezone.utc) - timedelta(hours=preset["window_h"])
        raw = downsample(load_history(rt.path, since=since), int(os.getenv("MAX_CHART_POINTS", "1500")))
        rows = [
            {"ts": r["ts"], "spread": r["spread_bps"], "hl": r["hl_mid"], "broker": r["broker_norm"]}
            for r in raw
        ]
    else:
        try:
            rows = _candle_series(rt.pair, rt.hl, rt.broker, tf)
        except Exception as exc:
            log.warning("[%s] candle series failed for %s: %s", sym, tf, exc)
            rows = []

    series = {
        "labels": [_fmt_label(r["ts"], preset["fmt"]) for r in rows],
        "spread": [round(r["spread"], 3) for r in rows],
        "diff": [round(r["hl"] - r["broker"], 4) for r in rows],
        "hl": [round(r["hl"], 4) for r in rows],
        "broker": [round(r["broker"], 4) for r in rows],
    }
    with _lock:
        current = dict(rt.latest)
    return jsonify(
        {
            "current": current,
            "series": series,
            "symbol": sym,
            "symbol_label": rt.pair.label,
            "hl_symbol": rt.pair.hl_symbol,
            "symbols": [
                {"key": k, "label": runtimes[k].pair.label} for k in order
            ],
            "entry_bps": cfg.entry_spread_bps,
            "exit_bps": cfg.exit_spread_bps,
            "timeframes": [{"key": t["key"], "label": t["label"]} for t in TIMEFRAMES],
            "tf": tf,
        }
    )


@app.route("/")
def index():
    return LANDING


@app.route("/app")
def app_page():
    return PAGE



def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    cfg = Config.load()
    app.config["CFG"] = cfg

    # Build a read-only runtime per symbol pair (the dashboard never trades).
    runtimes: dict[str, PairRuntime] = {}
    order: list[str] = []
    for i, pair in enumerate(cfg.pairs):
        rt = PairRuntime(cfg, pair, primary=(i == 0))
        runtimes[pair.key] = rt
        order.append(pair.key)
    app.config["RUNTIMES"] = runtimes
    app.config["ORDER"] = order

    for rt in runtimes.values():
        _backfill(cfg, rt)

    t = threading.Thread(target=_sampler, args=(cfg, list(runtimes.values())), daemon=True)
    t.start()

    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    url = f"http://{host}:{port}/"
    log.info(
        "Spread dashboard at %s  (symbols=%s, broker=%s)",
        url, ",".join(order), cfg.broker,
    )

    if os.getenv("DASHBOARD_NO_OPEN", "false").strip().lower() not in {"1", "true", "yes"}:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        app.run(host=host, port=port, threaded=True)
    finally:
        _stop.set()
    return 0


_STYLE = """
  :root{ --bg:#ffffff; --panel:#ffffff; --soft:#f6f7f9; --ink:#0b0b0c;
         --muted:#6b7280; --faint:#9ca3af; --line:#ececec; --line2:#e5e7eb;
         --accent:#16a34a; --indigo:#4f46e5; --pos:#16a34a; --neg:#dc2626;
         color-scheme: light; }
  *{ box-sizing:border-box; }
  html,body{ margin:0; }
  body{ font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',Roboto,sans-serif;
        color:var(--ink); background:var(--bg); -webkit-font-smoothing:antialiased; }
  a{ color:inherit; text-decoration:none; }
  .brand{ display:flex; align-items:center; gap:9px; font-weight:600; font-size:16px; }
  .mark{ width:18px; height:18px; border-radius:6px; display:inline-block;
         background:linear-gradient(135deg,#0b0b0c,#3b3b3f); }
"""

LANDING = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Spread — live HyperLiquid vs. Capital.com spreads</title>
<style>
""" + _STYLE + """
  .nav{ display:flex; align-items:center; justify-content:space-between;
        max-width:1120px; margin:0 auto; padding:20px 28px; }
  .navlinks{ display:flex; gap:26px; font-size:14px; color:#3b3b3f; }
  .navlinks a:hover{ color:#000; }
  .hero{ max-width:840px; margin:0 auto; padding:64px 24px 24px; text-align:center; }
  .badge{ display:inline-flex; align-items:center; gap:8px; font-size:12.5px; color:#52525b;
          border:1px solid var(--line); border-radius:999px; padding:6px 14px; margin-bottom:30px; }
  .dot{ width:7px; height:7px; border-radius:50%; background:var(--accent);
        box-shadow:0 0 0 3px rgba(22,163,74,.15); }
  h1{ font-size:64px; line-height:1.02; letter-spacing:-.025em; font-weight:600; margin:0 0 22px; }
  .sub{ font-size:18px; line-height:1.55; color:var(--muted); max-width:560px; margin:0 auto 36px; }
  .cta{ display:flex; gap:22px; align-items:center; justify-content:center; }
  .btn{ background:var(--ink); color:#fff; border-radius:999px; padding:14px 28px;
        font-size:15px; font-weight:500; cursor:pointer; transition:transform .08s ease, opacity .2s; }
  .btn:hover{ opacity:.88; transform:translateY(-1px); }
  .ghost{ font-size:15px; color:#3b3b3f; }
  .ghost:hover{ color:#000; }
  .strip{ max-width:1000px; margin:70px auto 0; padding:0 24px; text-align:center; }
  .striplabel{ font-size:11px; letter-spacing:.18em; color:var(--faint); font-weight:600; margin-bottom:22px; }
  .chips{ display:flex; flex-wrap:wrap; gap:12px; justify-content:center; }
  .chip{ border:1px solid var(--line); border-radius:14px; padding:13px 16px; min-width:104px;
         font-size:12px; color:var(--faint); background:#fff;
         transition:box-shadow .2s, transform .08s, border-color .2s; }
  .chip:hover{ box-shadow:0 10px 28px rgba(0,0,0,.07); transform:translateY(-2px); border-color:var(--line2); }
  .chip b{ display:block; font-size:14px; color:var(--ink); font-weight:600; margin-bottom:3px; }
  .how{ max-width:920px; margin:92px auto 84px; padding:0 24px;
        display:grid; grid-template-columns:repeat(3,1fr); gap:22px; }
  .step{ border:1px solid var(--line); border-radius:16px; padding:22px; background:#fff; }
  .step .n{ font-size:12px; color:var(--accent); font-weight:700; }
  .step h3{ font-size:16px; margin:8px 0 6px; }
  .step p{ font-size:13.5px; color:var(--muted); line-height:1.55; margin:0; }
  footer{ border-top:1px solid var(--line); text-align:center; color:var(--faint);
          font-size:12.5px; padding:26px; }
  @media(max-width:720px){ h1{ font-size:42px; } .how{ grid-template-columns:1fr; } .navlinks{ display:none; } }
</style>
</head>
<body>
  <nav class="nav">
    <div class="brand"><span class="mark"></span> Spread</div>
    <div class="navlinks">
      <a href="#markets">Markets</a>
      <a href="#how">How it works</a>
      <a href="/app">Dashboard</a>
    </div>
  </nav>
  <section class="hero">
    <div class="badge"><span class="dot"></span> <span id="badgetxt">Live · HyperLiquid vs. Capital.com</span></div>
    <h1>Live spreads,<br/>one screen.</h1>
    <p class="sub">Track the real-time price gap between HyperLiquid perps and Capital.com
       CFDs — across indices, commodities and crypto, updated every few seconds.</p>
    <div class="cta">
      <a class="btn" href="/app">Open the dashboard</a>
      <a class="ghost" href="#how">See how it works ›</a>
    </div>
  </section>
  <section class="strip" id="markets">
    <div class="striplabel">ANY MARKET. ANY VENUE. ONE SPREAD.</div>
    <div class="chips" id="chips"></div>
  </section>
  <section class="how" id="how">
    <div class="step"><div class="n">01</div><h3>Two venues, one price</h3>
      <p>We read the HyperLiquid mid and the Capital.com quote for the same instrument and
         normalize them onto a single scale.</p></div>
    <div class="step"><div class="n">02</div><h3>The spread, live</h3>
      <p>The gap between them — in points and basis points — is sampled continuously and
         charted across timeframes from seconds to a year.</p></div>
    <div class="step"><div class="n">03</div><h3>Spot dislocations</h3>
      <p>Entry / exit bands flag when the spread is wide enough to matter. It's read-only —
         the monitor never places a trade.</p></div>
  </section>
  <footer>Spread · read-only monitor · not investment advice</footer>
  <script>
    fetch('/api/spread').then(r=>r.json()).then(j=>{
      if(j.symbols && j.symbols.length){
        document.getElementById('chips').innerHTML = j.symbols
          .map(s=>`<a class="chip" href="/app?symbol=${s.key}"><b>${s.label}</b>HL ↔ CFD</a>`).join('');
        document.getElementById('badgetxt').textContent = `Live · ${j.symbols.length} markets tracked`;
      }
    }).catch(()=>{});
  </script>
</body>
</html>
"""


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Spread — dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
""" + _STYLE + """
  body{ background:var(--soft); }
  .topbar{ position:sticky; top:0; z-index:10; background:rgba(255,255,255,.85);
           backdrop-filter:saturate(180%) blur(10px); border-bottom:1px solid var(--line);
           display:flex; align-items:center; justify-content:space-between; gap:16px;
           padding:13px 24px; flex-wrap:wrap; }
  .topbar .brand{ color:var(--ink); }
  .controls{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  .field{ display:flex; flex-direction:column; gap:3px; }
  .field label{ font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:var(--faint); }
  select{ appearance:none; -webkit-appearance:none; background:#fff
          url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%236b7280' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E") no-repeat right 11px center;
          border:1px solid var(--line2); border-radius:10px; padding:7px 30px 7px 12px;
          font-size:13px; color:var(--ink); cursor:pointer; }
  select:focus{ outline:none; border-color:#cfcfd4; }
  .statuspill{ font-size:12px; color:var(--muted); display:flex; align-items:center; gap:7px; }
  .statuspill .dot{ width:7px; height:7px; border-radius:50%; background:var(--accent); }
  .statuspill.bad .dot{ background:var(--neg); }
  main{ max-width:1120px; margin:0 auto; padding:24px; }
  .metaline{ font-size:13px; color:var(--muted); margin:2px 0 18px; }
  .metaline b{ color:var(--ink); font-weight:600; }
  .cards{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:14px; margin-bottom:18px; }
  .card{ background:var(--panel); border:1px solid var(--line); border-radius:14px;
         padding:16px 18px; box-shadow:0 1px 2px rgba(0,0,0,.03); }
  .card.hero-card{ background:linear-gradient(180deg,#fff,#fbfbfc); }
  .card .k{ font-size:11px; color:var(--faint); text-transform:uppercase; letter-spacing:.05em; }
  .card .v{ font-size:28px; font-weight:700; margin-top:8px; font-variant-numeric:tabular-nums; letter-spacing:-.01em; }
  .card .sub{ font-size:12px; color:var(--faint); margin-top:5px; }
  .pos{ color:var(--pos); } .neg{ color:var(--neg); } .muted{ color:var(--faint); }
  .chart-wrap{ background:var(--panel); border:1px solid var(--line); border-radius:16px;
               padding:18px; box-shadow:0 1px 2px rgba(0,0,0,.03); }
</style>
</head>
<body>
<div class="topbar">
  <a class="brand" href="/"><span class="mark"></span> Spread</a>
  <div class="controls">
    <div class="field"><label for="sym">Symbol</label><select id="sym"></select></div>
    <div class="field"><label for="tf">Timeframe</label><select id="tf"></select></div>
    <span class="statuspill" id="status"><span class="dot"></span><span id="statustxt">connecting…</span></span>
  </div>
</div>
<main>
  <div class="metaline" id="meta"></div>
  <div class="cards">
    <div class="card hero-card"><div class="k">Current spread (pts)</div><div class="v" id="cur">—</div></div>
    <div class="card"><div class="k">HyperLiquid</div><div class="v" id="hl">—</div></div>
    <div class="card"><div class="k">Comparison (norm)</div><div class="v" id="bk">—</div></div>
    <div class="card"><div class="k">Funding (1h)</div><div class="v" id="fund">—</div><div class="sub" id="fundsub"></div></div>
  </div>
  <div class="chart-wrap"><canvas id="chart" height="108"></canvas></div>
</main>
<script>
const ctx = document.getElementById('chart');
const tfSelect = document.getElementById('tf');
const symSelect = document.getElementById('sym');
let chart, entryBps = 0, exitBps = 0;
let currentTf = '5m';
let currentSym = new URLSearchParams(location.search).get('symbol');
let tfReady = false;
let symReady = false;

tfSelect.addEventListener('change', () => { currentTf = tfSelect.value; refresh(); });
symSelect.addEventListener('change', () => { currentSym = symSelect.value; refresh(); });

function makeChart(){
  chart = new Chart(ctx, {
    type: 'line',
    data: { labels: [], datasets: [
      { label: 'Spread (price diff)', data: [], borderColor: '#4f46e5',
        backgroundColor: 'rgba(79,70,229,.10)', borderWidth: 1.8, pointRadius: 0,
        tension: .15, fill: true, yAxisID: 'y' },
    ]},
    options: {
      animation: false, responsive: true, interaction: { mode: 'index', intersect: false },
      scales: {
        x: { ticks: { color: '#9ca3af', maxTicksLimit: 10, autoSkip: true }, grid: { color: '#f0f1f3' } },
        y: { title: { display: true, text: 'price difference (HL − comparison)', color: '#9ca3af' },
             ticks: { color: '#9ca3af' }, grid: { color: '#f0f1f3' } },
      },
      plugins: { legend: { labels: { color: '#6b7280', usePointStyle: true, boxWidth: 8 } } },
    }
  });
}

function fmt(v, d=2){ return (v===null||v===undefined) ? '—' : Number(v).toFixed(d); }

async function refresh(){
  try {
    const params = new URLSearchParams({ tf: currentTf });
    if (currentSym) params.set('symbol', currentSym);
    const r = await fetch('/api/spread?' + params.toString());
    const j = await r.json();
    entryBps = j.entry_bps; exitBps = j.exit_bps;

    if (!tfReady && j.timeframes) {
      tfSelect.innerHTML = j.timeframes
        .map(t => `<option value="${t.key}">${t.label}</option>`).join('');
      tfSelect.value = j.tf; currentTf = j.tf; tfReady = true;
    }

    if (!symReady && j.symbols) {
      symSelect.innerHTML = j.symbols
        .map(s => `<option value="${s.key}">${s.label}</option>`).join('');
      symSelect.value = j.symbol; currentSym = j.symbol; symReady = true;
    }

    document.getElementById('meta').innerHTML =
      `<b>${j.symbol_label}</b> (${j.hl_symbol}) · entry ±${j.entry_bps} bps · exit ±${j.exit_bps} bps`;

    const c = j.current;
    const curEl = document.getElementById('cur');
    if (c.hl_mid != null && c.broker_norm != null) {
      const diff = c.hl_mid - c.broker_norm;
      curEl.textContent = (diff>=0?'+':'') + fmt(diff, 2) + ' pts';
      curEl.className = 'v ' + (Math.abs(c.spread_bps) >= entryBps
        ? (c.spread_bps>=0?'pos':'neg') : 'muted');
    }
    document.getElementById('hl').textContent = fmt(c.hl_mid, 4);
    document.getElementById('bk').textContent = fmt(c.broker_norm, 4);
    const fundEl = document.getElementById('fund');
    const fundSub = document.getElementById('fundsub');
    const f = c.funding;
    if (f == null) {
      fundEl.textContent = '—'; fundEl.className = 'v muted';
      fundSub.textContent = 'no funding (spot / unavailable)';
    } else {
      const hourlyPct = f * 100;
      const aprPct = f * 24 * 365 * 100;
      const sign = f >= 0 ? '+' : '';
      fundEl.textContent = sign + hourlyPct.toFixed(4) + '%/hr';
      fundEl.className = 'v ' + (f > 0 ? 'neg' : (f < 0 ? 'pos' : ''));
      fundSub.textContent = '≈ ' + sign + aprPct.toFixed(1) + '% / yr · ' +
        (f > 0 ? 'longs pay shorts' : (f < 0 ? 'shorts pay longs' : 'flat'));
    }
    const pill = document.getElementById('status');
    document.getElementById('statustxt').textContent = c.ok
      ? ('updated ' + (c.ts? new Date(c.ts).toLocaleTimeString():''))
      : ('error: ' + (c.error||'no data'));
    pill.className = 'statuspill' + (c.ok ? '' : ' bad');

    chart.data.labels = j.series.labels;
    chart.data.datasets[0].data = j.series.diff;
    chart.update();
  } catch (e) {
    document.getElementById('statustxt').textContent = 'fetch error: ' + e;
    document.getElementById('status').className = 'statuspill bad';
  }
}

makeChart();
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
