"""Weights-based sweep for roostoo_trend, scored like the Roostoo contest.

Not vectorbt signals: this is a multi-asset long/flat/short book with
fractional weights, so a plain hourly simulation is clearer and has no
hidden fill rules. Weights computed on bar t's close are held from bar
t+1. Every change in weight pays fee + slippage on |delta|.

Scoring: rolling 14-day windows (step 1 day), each scored with the
contest's composite 0.4*Sortino + 0.3*Sharpe + 0.3*Calmar. Annualization
basis is unknown (daily or hourly returns), so both are reported.
"""

import itertools
from pathlib import Path

import logic  # this folder's logic.py (the script's own dir is on sys.path)
import numpy as np
import pandas as pd
import yaml
from tabulate import tabulate

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
REPORT_DIR = HERE / "reports"


def load_closes(cfg: dict) -> pd.DataFrame:
    cols = {}
    for sym in cfg["symbols"]:
        path = DATA_DIR / cfg["timeframe"] / f"{sym}USDT.parquet"
        if not path.exists():
            raise FileNotFoundError(f"{path} missing. Run: uv run python {HERE.name}/fetch_data.py "
                                    f"--timeframe {cfg['timeframe']}")
        df = pd.read_parquet(path)
        cols[sym] = df.set_index(pd.to_datetime(df["timestamp"], unit="ms", utc=True))["close"]
    close = pd.DataFrame(cols).sort_index()
    full = pd.date_range(close.index[0], close.index[-1], freq="1h")
    missing = len(full) - len(close)
    if missing > 0:
        print(f"note: {missing} missing hourly rows reindexed (returns set to 0 there)")
    return close.reindex(full)


def simulate(close: pd.DataFrame, weights: pd.DataFrame, p: dict, cost: float) -> dict:
    """Hold `weights` row t from bar t+1, trading only on rebalance hours."""
    targets = weights.reindex(columns=close.columns).fillna(0.0).to_numpy()
    rets = close.pct_change(fill_method=None).fillna(0.0).to_numpy()
    rebalance = (close.index.hour % p["rebalance_hours"] == 0)
    n, m = rets.shape
    held = np.zeros(m)
    equity = np.empty(n)
    held_hist = np.zeros((n, m))
    turnover = np.zeros(n)
    eq = 1.0
    for t in range(n):
        port_ret = held @ rets[t]
        eq *= 1.0 + port_ret
        held = held * (1.0 + rets[t]) / (1.0 + port_ret)
        if rebalance[t]:
            peak = max(equity[max(0, t - p["dd_peak_hours"]):t].max(initial=eq), eq)
            want = targets[t] * logic.drawdown_scale(eq, peak, p)
            delta = np.where(np.abs(want - held) >= p["band"], want - held, 0.0)
            delta = np.where((want == 0) & (held != 0), -held, delta)  # always fully exit
            turnover[t] = np.abs(delta).sum()
            eq *= 1.0 - turnover[t] * cost
            held = held + delta
        equity[t] = eq
        held_hist[t] = held
    idx = close.index
    return {"equity": pd.Series(equity, idx), "held": pd.DataFrame(held_hist, idx, close.columns),
            "turnover": pd.Series(turnover, idx)}


def composite(rets: np.ndarray, mdd: float, periods: int) -> float:
    mu, sd = rets.mean(), rets.std(ddof=1)
    downside = np.sqrt(np.mean(np.minimum(rets, 0.0) ** 2))
    if sd == 0 or downside == 0 or mdd == 0:
        return np.nan
    sharpe = mu / sd * np.sqrt(periods)
    sortino = mu / downside * np.sqrt(periods)
    calmar = mu * periods / mdd
    return 0.4 * sortino + 0.3 * sharpe + 0.3 * calmar


def window_stats(sim: dict, start: pd.Timestamp, days: int) -> dict:
    end = start + pd.Timedelta(days=days)
    eq = sim["equity"].loc[start:end]
    eq = eq / eq.iloc[0]
    mdd = float(-(eq / eq.cummax() - 1.0).min())
    daily = eq.iloc[::24].pct_change().dropna().to_numpy()
    hourly = eq.pct_change().dropna().to_numpy()
    traded = sim["turnover"].loc[start:end] > 0
    held = sim["held"].loc[start:end]
    return {
        "ret": eq.iloc[-1] - 1.0, "mdd": mdd,
        "comp_d": composite(daily, mdd, 365), "comp_h": composite(hourly, mdd, 365 * 24),
        "trade_days": traded[traded].index.normalize().nunique(),
        "has_short": bool((held < 0).to_numpy().any()),
    }


def summarize(sim: dict, start: str | None, end: str | None, days: int) -> dict:
    lo = pd.Timestamp(start, tz="UTC") if start else sim["equity"].index[0]
    hi = pd.Timestamp(end, tz="UTC") if end else sim["equity"].index[-1]
    starts = pd.date_range(lo, hi - pd.Timedelta(days=days), freq="1D")
    w = pd.DataFrame([window_stats(sim, s, days) for s in starts])
    eq = sim["equity"].loc[lo:hi]
    years = len(eq) / (365 * 24)
    return {
        "windows": len(w), "total_ret": eq.iloc[-1] / eq.iloc[0] - 1.0,
        "turnover_yr": sim["turnover"].loc[lo:hi].sum() / years,
        "avg_gross": sim["held"].loc[lo:hi].abs().sum(axis=1).mean(),
        "med_ret": w["ret"].median(), "p10_ret": w["ret"].quantile(0.1),
        "pct_pos": (w["ret"] > 0).mean(),
        "med_mdd": w["mdd"].median(), "worst_mdd": w["mdd"].max(),
        "med_comp_d": w["comp_d"].median(), "med_comp_h": w["comp_h"].median(),
        "pct_10days": (w["trade_days"] >= 10).mean(), "pct_short": w["has_short"].mean(),
    }


def benchmark(close: pd.DataFrame, oos_start: str, days: int) -> list[dict]:
    rows = []
    for name, weights in [("BTC hold", {"BTC": 1.0}), ("EW hold", None)]:
        rets = close.pct_change(fill_method=None)
        r = rets["BTC"] if weights else rets.mean(axis=1)
        eq = (1.0 + r.fillna(0.0)).cumprod()
        sim = {"equity": eq, "turnover": pd.Series(0.0, eq.index),
               "held": pd.DataFrame(0.0, eq.index, ["x"])}
        rows.append({"name": name, **summarize(sim, None, oos_start, days)})
        rows.append({"name": name + " OOS", **summarize(sim, oos_start, None, days)})
    return rows


def run_grid(close: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    cost = cfg["fee"] + cfg["slippage"]
    keys = list(cfg["grid"])
    rows = []
    for values in itertools.product(*(cfg["grid"][k] for k in keys)):
        p = {**cfg["params"], **dict(zip(keys, values))}
        sim = simulate(close, logic.target_weights(close, p), p, cost)
        tag = {k: str(v) for k, v in zip(keys, values)}
        ins = summarize(sim, None, cfg["oos_start"], cfg["window_days"])
        oos = summarize(sim, cfg["oos_start"], None, cfg["window_days"])
        rows.append({**tag, **{f"is_{k}": v for k, v in ins.items()},
                     **{f"oos_{k}": v for k, v in oos.items()}})
        print(f"done {tag} IS comp_d={ins['med_comp_d']:.2f} OOS comp_d={oos['med_comp_d']:.2f}")
    return pd.DataFrame(rows)


def fmt(df: pd.DataFrame) -> str:
    return tabulate(df, headers="keys", tablefmt="github", floatfmt=".3f", showindex=False)


def main() -> None:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    close = load_closes(cfg)
    print(f"{cfg['strategy_name']}: {close.shape[1]} coins, {close.index[0]} -> {close.index[-1]}")
    res = run_grid(close, cfg).sort_values("is_med_comp_d", ascending=False)
    keys = list(cfg["grid"])
    show = ["med_ret", "p10_ret", "med_mdd", "med_comp_d", "med_comp_h", "pct_10days",
            "pct_short", "total_ret", "turnover_yr", "avg_gross"]
    ins = res[keys + [f"is_{c}" for c in show]]
    oos = res[keys + [f"oos_{c}" for c in show]]
    bench = pd.DataFrame(benchmark(close, cfg["oos_start"], cfg["window_days"]))
    bench = bench[["name"] + show]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    res.to_csv(REPORT_DIR / "sweep.csv", index=False)
    text = (f"{cfg['strategy_name']} sweep, {cfg['window_days']}-day rolling windows, "
            f"cost {cfg['fee'] + cfg['slippage']:.4f}/side\n"
            f"data {close.index[0]} -> {close.index[-1]}, OOS from {cfg['oos_start']}\n\n"
            f"In-sample (sorted by median daily-basis composite):\n{fmt(ins)}\n\n"
            f"Out-of-sample (same row order):\n{fmt(oos)}\n\n"
            f"Benchmarks:\n{fmt(bench)}\n")
    (REPORT_DIR / "report.txt").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
