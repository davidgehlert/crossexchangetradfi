"""Read-only market-data source for the comparison leg via the Capital.com API.

Compares the HyperLiquid S&P 500 perp against Capital.com's `US500` CFD (the
S&P 500 index, ~1:1 scale). Uses Capital.com's REST API with a demo or live
account — works on macOS, no MetaTrader/Windows required.

Auth flow (per Capital.com docs):
  1. POST /api/v1/session  with header X-CAP-API-KEY and body {identifier,
     password}. The response headers carry CST and X-SECURITY-TOKEN.
  2. Send those two tokens as headers on subsequent data requests.
Session tokens expire after ~10 min of inactivity, so we re-login on 401.

Implements the Broker interface for prices/history only; trading methods raise
(this is for monitoring the spread, not executing).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .base import Broker, BrokerPosition

log = logging.getLogger("capital")

_LIVE = "https://api-capital.backend-capital.com"
_DEMO = "https://demo-api-capital.backend-capital.com"

_RESOLUTIONS = {
    1: "MINUTE",
    5: "MINUTE_5",
    15: "MINUTE_15",
    30: "MINUTE_30",
    60: "HOUR",
    240: "HOUR_4",
    1440: "DAY",
}


class CapitalBroker(Broker):
    def __init__(
        self,
        api_key: str,
        identifier: str,
        password: str,
        epic: str = "US500",
        demo: bool = True,
        price_cache_ttl: float = 3.0,
    ) -> None:
        self.symbol = epic
        self.epic = epic
        self._api_key = api_key
        self._identifier = identifier
        self._password = password
        self._base = _DEMO if demo else _LIVE
        self._ttl = price_cache_ttl

        self._cst: str | None = None
        self._security_token: str | None = None
        self._cached_price: float | None = None
        self._cached_at = 0.0
        # Shared between the dashboard sampler thread and web request threads.
        self._lock = threading.RLock()
        log.info("Capital.com client ready (epic=%s, demo=%s)", epic, demo)

    # ----- low-level HTTP -------------------------------------------------
    def _request(self, method: str, path: str, body: dict | None = None,
                 auth: bool = True) -> tuple[int, dict, dict]:
        url = self._base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-CAP-API-KEY", self._api_key)
        req.add_header("Content-Type", "application/json")
        if auth:
            if not self._cst or not self._security_token:
                self._login()
            req.add_header("CST", self._cst)
            req.add_header("X-SECURITY-TOKEN", self._security_token)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = resp.read()
                parsed = json.loads(payload) if payload else {}
                return resp.status, parsed, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"Capital.com {method} {path} -> HTTP {exc.code}: {detail}") from None

    def _login(self) -> None:
        url = self._base + "/api/v1/session"
        body = json.dumps({"identifier": self._identifier, "password": self._password}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("X-CAP-API-KEY", self._api_key)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                headers = dict(resp.headers)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(
                f"Capital.com login failed (HTTP {exc.code}): {detail}. "
                f"Check CAPITAL_API_KEY / CAPITAL_IDENTIFIER / CAPITAL_PASSWORD "
                f"and that CAPITAL_DEMO matches the account type."
            ) from None
        self._cst = headers.get("CST")
        self._security_token = headers.get("X-SECURITY-TOKEN")
        if not self._cst or not self._security_token:
            raise RuntimeError("Capital.com login returned no session tokens")
        log.info("Capital.com session established")

    def _get_with_retry(self, path: str) -> dict:
        try:
            _, parsed, _ = self._request("GET", path, auth=True)
            return parsed
        except RuntimeError as exc:
            # Session likely expired -> re-login once and retry.
            if "HTTP 401" in str(exc):
                self._cst = self._security_token = None
                _, parsed, _ = self._request("GET", path, auth=True)
                return parsed
            raise

    # ----- market data ----------------------------------------------------
    def get_mid_price(self) -> float:
        with self._lock:
            now = time.time()
            if self._cached_price is not None and (now - self._cached_at) < self._ttl:
                return self._cached_price
            data = self._get_with_retry(f"/api/v1/markets/{urllib.parse.quote(self.epic)}")
            snap = data.get("snapshot", {})
            bid, offer = snap.get("bid"), snap.get("offer")
            if bid is None or offer is None:
                raise RuntimeError(f"No bid/offer for {self.epic}: {data}")
            self._cached_price = (float(bid) + float(offer)) / 2.0
            self._cached_at = now
            return self._cached_price

    def get_history(self, start, end, interval_minutes: int = 5):
        resolution = _RESOLUTIONS.get(interval_minutes, "MINUTE_5")
        params = urllib.parse.urlencode(
            {
                "resolution": resolution,
                "from": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "to": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                "max": 1000,
            }
        )
        with self._lock:
            data = self._get_with_retry(f"/api/v1/prices/{urllib.parse.quote(self.epic)}?{params}")
        out = []
        for p in data.get("prices", []):
            try:
                raw_ts = p.get("snapshotTimeUTC") or p.get("snapshotTime")
                ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                close = p["closePrice"]
                mid = (float(close["bid"]) + float(close["ask"])) / 2.0
                out.append((ts, mid))
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
        return out

    # ----- account / trading (unsupported: data-only) --------------------
    def get_position(self) -> BrokerPosition | None:
        return None

    def _readonly(self, *_a, **_k):
        raise NotImplementedError(
            "CapitalBroker is a read-only data feed (monitoring only); it cannot trade yet."
        )

    market_buy = _readonly
    market_sell = _readonly
    close_position = _readonly
