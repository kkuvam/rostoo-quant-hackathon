"""Run every candidate strategy through the same contest scoring and rank them.

Same simulation as vector_sweep.py (one-bar lag, 0.13% per side on every weight
change, daily rebalance unless a grid says otherwise), same rolling 14-day
windows. Ranked by in-sample median daily-basis composite; out-of-sample
columns are for checking, not for picking.

  uv run python roostoo_hackathon/compare.py                  # every candidate
  uv run python roostoo_hackathon/compare.py funding_contra   # just one family
  uv run python roostoo_hackathon/compare.py --market         # taker costs, not limit
"""

import itertools
import sys

import candidates as cand
import logic
import pandas as pd
import yaml
from vector_sweep import HERE, REPORT_DIR, fmt, load_closes, simulate, summarize, trade_costs

# Small grids on purpose: a few structural choices per idea, not a fit.
GRIDS = {
    "ew_hold": (cand.ew_hold, {}),
    "trend": (logic.target_weights, {}),
    "vol_beta": (cand.vol_beta, {"vol_target": [0.3, 0.6], "ema_hours": [168, 504],
                                 "long_only": [True, False]}),
    "xs_momentum": (cand.xs_momentum, {"lookback": [72, 168], "k": [3, 5],
                                       "ema_hours": [168, 504]}),
    "xs_reversal": (cand.xs_reversal, {"lookback": [24, 72], "k": [3, 5], "ema_hours": [168]}),
    "donchian": (cand.donchian, {"channel": [168, 480], "long_only": [True, False]}),
    "zscore_mr": (cand.zscore_mr, {"window": [24, 72], "z_entry": [2.0],
                                   "long_only": [True, False], "rebalance_hours": [4]}),
    # Binance default funding is 0.01% per 8h; 0.01% away from it maps to a full position.
    "funding_contra": (cand.funding_contra, {"funding_hours": [24, 72], "neutral": [0.0001],
                                             "scale": [0.0001], "long_only": [True, False]}),
    "funding_basket": (cand.funding_basket, {"funding_hours": [24, 72], "neutral": [0.0001],
                                             "scale": [0.0001], "long_only": [True, False]}),
}
BASE = {"warmup": 720, "vol_window": 168, "dd_limit": 0.0}


def load_funding(cfg: dict, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Hourly panel of each coin's latest funding rate, normalized to a per-8h rate.

    A payment at time T is placed on the bar that opens at T (it closes an hour
    later), so the strategy only sees it after it is public.
    """
    cols = {}
    for sym in cfg["symbols"]:
        path = HERE / "data" / "funding" / f"{sym}USDT.parquet"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing. Run: uv run python {HERE.name}/fetch_data.py "
                                    "--funding")
        df = pd.read_parquet(path)
        ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.floor("1h")
        interval_h = ts.diff().dt.total_seconds().div(3600).fillna(8.0).clip(lower=1.0)
        rate = pd.Series((df["rate"] * 8.0 / interval_h).to_numpy(), index=ts)
        cols[sym] = rate[~rate.index.duplicated(keep="last")]
    return pd.DataFrame(cols).reindex(index).ffill()
SHOW = ["med_ret", "p10_ret", "pct_pos", "med_mdd", "med_comp_d", "pct_10days", "pct_short",
        "total_ret", "turnover_yr", "avg_gross"]


def run_all(close: pd.DataFrame, cfg: dict, names: list[str], mode: str) -> pd.DataFrame:
    cost = trade_costs(cfg, mode)
    base = {**BASE, "funding": load_funding(cfg, close.index)}
    rows = []
    for name in names:
        func, grid = GRIDS[name]
        keys = list(grid)
        for values in itertools.product(*(grid[k] for k in keys)):
            over = dict(zip(keys, values))
            p = {**cfg["params"], **base, **over}
            sim = simulate(close, func(close, p), p, cost)
            ins = summarize(sim, None, cfg["oos_start"], cfg["window_days"])
            oos = summarize(sim, cfg["oos_start"], None, cfg["window_days"])
            label = name + "".join(f" {k}={v}" for k, v in over.items())
            rows.append({"strategy": label, **{f"is_{k}": ins[k] for k in SHOW},
                         **{f"oos_{k}": oos[k] for k in SHOW}})
            print(f"{label}: IS comp {ins['med_comp_d']:.2f} OOS comp {oos['med_comp_d']:.2f}",
                  flush=True)
    return pd.DataFrame(rows).sort_values("is_med_comp_d", ascending=False)


def main() -> None:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    close = load_closes(cfg)
    picked = [a for a in sys.argv[1:] if not a.startswith("--")]
    names = picked or list(GRIDS)
    mode = "market" if "--market" in sys.argv else cfg["cost_mode"]
    res = run_all(close, cfg, names, mode)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"compare_{mode}" + ("_" + "_".join(picked) if picked else "")
    res.to_csv(REPORT_DIR / f"{stem}.csv", index=False)
    ins = res[["strategy"] + [f"is_{c}" for c in SHOW]]
    oos = res[["strategy"] + [f"oos_{c}" for c in SHOW]]
    text = (f"Candidates, {cfg['window_days']}-day windows, OOS from {cfg['oos_start']}, "
            f"{mode} orders, costs (long, short) {trade_costs(cfg, mode)}\n\n"
            f"In-sample:\n{fmt(ins)}\n\nOut-of-sample (same order):\n{fmt(oos)}\n")
    (REPORT_DIR / f"{stem}.txt").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
