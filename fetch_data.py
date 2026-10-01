"""Download Binance spot klines into roostoo_hackathon/data/<tf>/<SYM>USDT.parquet.

Source is Binance's bulk archive (data.binance.vision): one zip per symbol per
month, or per day for the current month (monthly files appear a few days after
month end). No API key, no rate-limit pagination. Roostoo's /USD prices track
Binance's USDT pairs within ~2 bps (checked live 2026-10-01).

Rerunning resumes from the first day of the last stored month, so it is cheap
to refresh before a backtest or before the live bot starts.

  uv run python roostoo_hackathon/fetch_data.py --timeframe 1m
  uv run python roostoo_hackathon/fetch_data.py --timeframe 1h --symbols BTC ETH
"""

import argparse
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import yaml

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
BASE_URL = "https://data.binance.vision/data/spot"
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def download_csv(url: str) -> pd.DataFrame | None:
    """One archive zip as a DataFrame, or None if Binance has no file there (404)."""
    res = requests.get(url, timeout=60)
    if res.status_code == 404:
        return None
    res.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        raw = zf.read(zf.namelist()[0])
    df = pd.read_csv(io.BytesIO(raw), header=None, usecols=range(6), names=COLUMNS)
    if not str(df.iloc[0, 0]).isdigit():  # some archive files carry a header row
        df = df.iloc[1:].astype(float)
    return df


def archive_urls(pair: str, tf: str, first: date, today: date) -> list[str]:
    """Archive URLs from `first` to yesterday: monthly zips, daily for the newest month."""
    urls, month = [], first.replace(day=1)
    while month <= today:
        nxt = (month + timedelta(days=32)).replace(day=1)
        name = f"{pair}-{tf}-{month:%Y-%m}"
        urls.append(f"{BASE_URL}/monthly/klines/{pair}/{tf}/{name}.zip")
        if nxt > today - timedelta(days=7):  # monthly file may not be published yet
            day = month
            while day < min(nxt, today):
                urls.append(f"{BASE_URL}/daily/klines/{pair}/{tf}/{pair}-{tf}-{day:%Y-%m-%d}.zip")
                day += timedelta(days=1)
        month = nxt
    return urls


def fetch_symbol(sym: str, tf: str, since: date, workers: int) -> Path:
    pair = f"{sym}USDT"
    out = DATA_DIR / tf / f"{pair}.parquet"
    old = pd.read_parquet(out) if out.exists() else None
    start = since
    if old is not None and len(old):
        start = pd.to_datetime(old["timestamp"].iloc[-1], unit="ms").date().replace(day=1)
    urls = archive_urls(pair, tf, start, date.today())
    with ThreadPoolExecutor(workers) as pool:
        frames = [f for f in pool.map(download_csv, urls) if f is not None]
    if not frames and old is None:
        raise RuntimeError(f"No Binance archive data for {pair} {tf} since {since}. "
                           f"Check the symbol is listed on Binance spot: {BASE_URL}/monthly/klines/{pair}/")
    df = pd.concat(([old] if old is not None else []) + frames, ignore_index=True)
    ts = df["timestamp"].astype("int64")
    df["timestamp"] = ts.where(ts < 10**14, ts // 1000)  # archive uses microseconds from 2025
    df = df.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    out.parent.mkdir(parents=True, exist_ok=True)
    df[COLUMNS].to_parquet(out, index=False)
    first, last = (pd.to_datetime(df["timestamp"].iloc[i], unit="ms") for i in (0, -1))
    print(f"{pair} {tf}: {len(df):,} bars {first} -> {last}")
    return out


def main() -> None:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--timeframe", default=cfg["timeframe"], help="Binance interval, e.g. 1m, 1h")
    p.add_argument("--symbols", nargs="+", default=cfg["symbols"], help="base coins, e.g. BTC ETH")
    p.add_argument("--since", default="2024-01-01", help="ISO date for a cold start")
    p.add_argument("--workers", type=int, default=8, help="parallel downloads per symbol")
    args = p.parse_args()
    for sym in args.symbols:
        fetch_symbol(sym, args.timeframe, date.fromisoformat(args.since), args.workers)


if __name__ == "__main__":
    main()
