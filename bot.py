"""Live Roostoo bot: funding_contra, long-only, daily rebalance after the 00:00 UTC bar.

    uv run python bot.py --dry-run   # print targets and orders, place nothing
    uv run python bot.py --once      # one limit pass now, market sweep after the wait, exit
    uv run python bot.py             # run forever (systemd)

Keys come from .env next to this file: ROOSTOO_API_KEY and ROOSTOO_SECRET_KEY.
"""

import argparse
import hashlib
import hmac
import json
import logging
import math
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yaml

HERE = Path(__file__).resolve().parent
ROOSTOO_URL = "https://mock-api.roostoo.com"
FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
STATE_PATH = HERE / "state.json"
OK_ERRORS = ("no pending order", "no order matched")
log = logging.getLogger("bot")


class Roostoo:
    """Roostoo v3 REST client. Signed calls: HMAC-SHA256 over the sorted params."""

    def __init__(self, key: str, secret: str):
        self.key, self.secret = key, secret.encode()
        self.http = requests.Session()

    def _sign(self, params: dict) -> tuple[str, dict]:
        params = {**params, "timestamp": str(int(time.time() * 1000))}
        body = "&".join(f"{k}={params[k]}" for k in sorted(params))
        sig = hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
        return body, {"RST-API-KEY": self.key, "MSG-SIGNATURE": sig}

    def _call(self, method: str, path: str, params: dict | None = None) -> dict:
        body, headers = self._sign(params or {})
        if method == "GET":
            r = self.http.get(f"{ROOSTOO_URL}{path}?{body}", headers=headers, timeout=15)
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            r = self.http.post(f"{ROOSTOO_URL}{path}", data=body, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("Success") is False and not data.get("ErrMsg", "").startswith(OK_ERRORS):
            raise RuntimeError(f"Roostoo {path} {params} failed: {data.get('ErrMsg')}")
        return data

    def exchange_info(self) -> dict:
        return self.http.get(f"{ROOSTOO_URL}/v3/exchangeInfo", timeout=15).json()["TradePairs"]

    def prices(self) -> dict[str, float]:
        data = self._call("GET", "/v3/ticker")["Data"]
        return {pair.split("/")[0]: float(t["LastPrice"]) for pair, t in data.items()}

    def wallet(self) -> dict[str, float]:
        w = self._call("GET", "/v3/balance")["Wallet"]
        return {c: float(v["Free"]) + float(v["Lock"]) for c, v in w.items()}

    def free_usd(self) -> float:
        return float(self._call("GET", "/v3/balance")["Wallet"]["USD"]["Free"])

    def cancel_all(self) -> None:
        pairs = self._call("GET", "/v3/pending_count").get("OrderPairs") or {}
        for pair in pairs:
            res = self._call("POST", "/v3/cancel_order", {"pair": pair})
            log.info("cancelled %s: %s", pair, res.get("CanceledList"))

    def order(self, coin: str, side: str, qty: str, price: str | None) -> dict:
        params = {
            "pair": f"{coin}/USD",
            "side": side,
            "quantity": qty,
            "type": "LIMIT" if price else "MARKET",
        }
        if price:
            params["price"] = price
        return self._call("POST", "/v3/place_order", params)["OrderDetail"]


def load_env() -> tuple[str, str]:
    path = HERE / ".env"
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    key = env.get("ROOSTOO_API_KEY") or os.environ.get("ROOSTOO_API_KEY")
    secret = env.get("ROOSTOO_SECRET_KEY") or os.environ.get("ROOSTOO_SECRET_KEY")
    if not key or not secret:
        raise SystemExit(f"ROOSTOO_API_KEY / ROOSTOO_SECRET_KEY missing. Put them in {path}.")
    return key, secret


def funding_avg(sym: str, bar: datetime, hours: int) -> float:
    """Mean per-8h funding over the `hours` hourly bars that open up to `bar`, as in backtest."""
    start = int((bar - timedelta(hours=hours + 48)).timestamp() * 1000)
    r = requests.get(
        FUNDING_URL, params={"symbol": f"{sym}USDT", "startTime": start, "limit": 1000}, timeout=15
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError(f"No Binance funding for {sym}USDT since {start}. Is the IP blocked?")
    ts = pd.to_datetime([x["fundingTime"] for x in rows], unit="ms", utc=True).floor("1h")
    interval_h = pd.Series(ts).diff().dt.total_seconds().div(3600).fillna(8.0).clip(lower=1.0)
    raw = pd.Series([float(x["fundingRate"]) for x in rows])
    rate = pd.Series((raw * 8.0 / interval_h).to_numpy(), ts)
    rate = rate[~rate.index.duplicated(keep="last")]
    grid = pd.date_range(end=pd.Timestamp(bar), periods=hours, freq="1h")
    return float(rate.reindex(rate.index.union(grid)).ffill().reindex(grid).mean())


def targets(cfg: dict, bar: datetime) -> dict[str, float]:
    live = cfg["live"]
    slot = cfg["params"]["max_gross"] / len(cfg["symbols"])
    out = {}
    for sym in cfg["symbols"]:
        avg = funding_avg(sym, bar, live["funding_hours"])
        score = min(max(-(avg - live["neutral"]) / live["scale"], 0.0), 1.0)
        out[sym] = score * slot
        log.info("%-5s funding %+.5f%%/8h  target %.4f", sym, avg * 100, out[sym])
    log.info("gross target %.3f", sum(out.values()))
    return out


def plan(want: dict, wallet: dict, px: dict, band: float) -> list[tuple[str, str, float]]:
    """Orders (coin, side, usd) with the backtest's band rule. Sells first."""
    equity = wallet.get("USD", 0.0) + sum(q * px[c] for c, q in wallet.items() if c in px)
    orders = []
    for coin, w in want.items():
        held = wallet.get(coin, 0.0) * px[coin] / equity
        if abs(w - held) >= band or (w == 0 and held > 0):
            orders.append((coin, "SELL" if w < held else "BUY", abs(w - held) * equity))
    log.info("equity %.2f USD, %d orders planned", equity, len(orders))
    return sorted(orders, key=lambda o: o[1] != "SELL")


def fmt(x: float, prec: int) -> str:
    return f"{math.floor(x * 10**prec) / 10**prec:.{prec}f}"


def execute(api: Roostoo, orders: list, px: dict, info: dict, limit: bool) -> int:
    """Place orders; buys are scaled to the free USD. Returns the number placed."""
    wallet = api.wallet()
    buy_usd = sum(u for _, s, u in orders if s == "BUY")
    scale = min(1.0, api.free_usd() * 0.995 / buy_usd) if buy_usd else 1.0
    placed = 0
    for coin, side, usd in orders:
        rules = info[f"{coin}/USD"]
        qty = (
            wallet.get(coin, 0.0)
            if side == "SELL" and usd >= wallet.get(coin, 0) * px[coin]
            else usd * (scale if side == "BUY" else 1.0) / px[coin]
        )
        qty_s = fmt(qty, rules["AmountPrecision"])
        if float(qty_s) * px[coin] < rules["MiniOrder"]:
            continue
        price = f"{px[coin]:.{rules['PricePrecision']}f}" if limit else None
        d = api.order(coin, side, qty_s, price)
        log.info(
            "%s %s %s @ %s -> %s id %s",
            side,
            qty_s,
            coin,
            price or "MKT",
            d.get("Status"),
            d.get("OrderID"),
        )
        placed += 1
    return placed


def rebalance(api: Roostoo, cfg: dict, want: dict, limit: bool) -> int:
    api.cancel_all()
    px = api.prices()
    orders = plan(want, api.wallet(), px, cfg["live"]["band"])
    return execute(api, orders, px, api.exchange_info(), limit)


def activity_trade(api: Roostoo, cfg: dict, want: dict) -> None:
    """Contest needs a trade every day: buy a small amount of the largest target (or BTC)."""
    coin = max(want, key=want.get) if any(want.values()) else "BTC"
    px = api.prices()[coin]
    prec = api.exchange_info()[f"{coin}/USD"]["AmountPrecision"]
    qty = fmt(max(cfg["live"]["activity_usd"] / px, 10**-prec), prec)
    d = api.order(coin, "BUY", qty, None)
    log.info("activity BUY %s %s -> %s", qty, coin, d.get("Status"))


def daily_bar(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}


def step(api: Roostoo, cfg: dict, state: dict, now: datetime) -> None:
    """One scheduler tick: limit pass after the rebalance hour, market sweep after the wait."""
    live, day = cfg["live"], now.date().isoformat()
    if now.hour < live["rebalance_hour_utc"]:
        return
    if state.get("limit_day") != day:
        want = targets(cfg, daily_bar(now))
        if rebalance(api, cfg, want, limit=True) == 0:
            activity_trade(api, cfg, want)
        state.update(limit_day=day, limit_at=now.isoformat(), want=want)
    elif state.get("market_day") != day:
        if now - datetime.fromisoformat(state["limit_at"]) < timedelta(
            minutes=live["market_after_min"]
        ):
            return
        rebalance(api, cfg, state["want"], limit=False)
        state["market_day"] = day
    else:
        return
    STATE_PATH.write_text(json.dumps(state, indent=1))


def dry_run(cfg: dict) -> None:
    want = targets(cfg, daily_bar(datetime.now(UTC)))
    if (HERE / ".env").exists() or os.environ.get("ROOSTOO_API_KEY"):
        api = Roostoo(*load_env())
        wallet, px = api.wallet(), api.prices()
    else:
        log.info("no keys: planning against a fresh 100k USD wallet")
        wallet = {"USD": float(cfg["initial_cash"])}
        px = {c: float(t["LastPrice"]) for c, t in public_ticker().items()}
    for coin, side, usd in plan(want, wallet, px, cfg["live"]["band"]):
        log.info("would %s %-5s %10.2f USD", side, coin, usd)


def public_ticker() -> dict:
    ts = str(int(time.time() * 1000))
    data = requests.get(f"{ROOSTOO_URL}/v3/ticker", params={"timestamp": ts}, timeout=15).json()
    return {pair.split("/")[0]: t for pair, t in data["Data"].items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print targets and orders only")
    ap.add_argument("--once", action="store_true", help="rebalance now, sweep, then exit")
    args = ap.parse_args()
    (HERE / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(HERE / "logs" / "bot.log"), logging.StreamHandler()],
    )
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    if args.dry_run:
        return dry_run(cfg)
    api = Roostoo(*load_env())
    if args.once:
        want = targets(cfg, daily_bar(datetime.now(UTC)))
        if rebalance(api, cfg, want, limit=True) == 0:
            activity_trade(api, cfg, want)
        time.sleep(cfg["live"]["market_after_min"] * 60)
        rebalance(api, cfg, want, limit=False)
        return
    log.info("bot started")
    state = load_state()
    while True:
        try:
            step(api, cfg, state, datetime.now(UTC))
        except Exception:
            log.exception("tick failed, retrying in 60s")
        time.sleep(60)


if __name__ == "__main__":
    main()
