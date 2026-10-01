"""Candidate strategies for the Roostoo contest, as target-weight functions.

Each function maps hourly closes (columns = coins, NaN before listing) and a
param dict to target weights (fraction of equity, short < 0). Row t may use
only closes up to bar t; vector_sweep.simulate holds it from bar t+1.
All are directional (long and/or short on price views), never market-making.
"""

import numpy as np
import pandas as pd

HOURS_PER_YEAR = 365 * 24


def live_mask(close: pd.DataFrame, warmup: int) -> pd.DataFrame:
    """True once a coin has `warmup` hours of history (no trading new listings cold)."""
    return close.notna() & close.shift(warmup).notna()


def basket_index(close: pd.DataFrame) -> pd.Series:
    """Equal-weight index of all listed coins (the 'market')."""
    rets = close.pct_change(fill_method=None).mean(axis=1).fillna(0.0)
    return (1.0 + rets).cumprod()


def regime(close: pd.DataFrame, ema_hours: int) -> pd.Series:
    """+1 when the basket is above its EMA, -1 below."""
    log_idx = np.log(basket_index(close))
    return np.sign(log_idx - log_idx.ewm(span=ema_hours, min_periods=ema_hours).mean()).fillna(0.0)


def equal_slots(mask: pd.DataFrame) -> pd.DataFrame:
    return mask.astype(float).div(mask.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)


def ew_hold(close: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Benchmark: always long every live coin, equal weight."""
    return equal_slots(live_mask(close, p["warmup"])) * p["max_gross"]


def vol_beta(close: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Equal-weight basket, gross set by basket vol target, direction by basket trend."""
    log_idx = np.log(basket_index(close))
    vol = log_idx.diff().rolling(p["vol_window"]).std() * np.sqrt(HOURS_PER_YEAR)
    gross = (p["vol_target"] / vol).clip(upper=p["max_gross"]).fillna(0.0)
    side = regime(close, p["ema_hours"])
    if p.get("long_only"):
        side = side.clip(lower=0.0)
    return equal_slots(live_mask(close, p["warmup"])).mul(gross * side, axis=0)


def xs_momentum(close: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Long the k strongest coins in an up regime, short the k weakest in a down regime."""
    mom = (close / close.shift(p["lookback"]) - 1.0).where(live_mask(close, p["warmup"]))
    side = regime(close, p["ema_hours"])
    top = mom.rank(axis=1, ascending=False) <= p["k"]
    bottom = mom.rank(axis=1, ascending=True) <= p["k"]
    longs = top.mul(side > 0, axis=0)
    shorts = bottom.mul(side < 0, axis=0) if not p.get("long_only") else bottom & False
    return (longs.astype(float) - shorts.astype(float)) * p["max_gross"] / p["k"]


def xs_reversal(close: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Buy the k biggest losers over `lookback` hours, only in an up regime."""
    mom = (close / close.shift(p["lookback"]) - 1.0).where(live_mask(close, p["warmup"]))
    losers = mom.rank(axis=1, ascending=True) <= p["k"]
    side = regime(close, p["ema_hours"])
    return losers.mul(side > 0, axis=0).astype(float) * p["max_gross"] / p["k"]


def donchian(close: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Per coin: +1 on a break above the prior N-hour high, -1 below the low, else hold."""
    hi = close.shift(1).rolling(p["channel"]).max()
    lo = close.shift(1).rolling(p["channel"]).min()
    sig = pd.DataFrame(np.nan, close.index, close.columns)
    sig = sig.mask(close > hi, 1.0).mask(close < lo, -1.0).ffill().fillna(0.0)
    if p.get("long_only"):
        sig = sig.clip(lower=0.0)
    sig = sig.where(live_mask(close, p["warmup"]), 0.0)
    return sig * p["max_gross"] / close.shape[1]


def zscore_mr(close: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Per coin: fade the z-score of price vs its N-hour mean, scaled to +-1 at z_entry."""
    mean = close.rolling(p["window"]).mean()
    std = close.rolling(p["window"]).std()
    sig = -((close - mean) / std / p["z_entry"]).clip(-1.0, 1.0)
    if p.get("long_only"):
        sig = sig.clip(lower=0.0)
    sig = sig.where(live_mask(close, p["warmup"]), 0.0).fillna(0.0)
    return sig * p["max_gross"] / close.shape[1]


def _funding_signal(funding: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Contrarian score in [-1, 1]: crowded longs (high funding) -> negative."""
    avg = funding.rolling(p["funding_hours"], min_periods=p["funding_hours"] // 2).mean()
    return -((avg - p["neutral"]) / p["scale"]).clip(-1.0, 1.0)


def funding_contra(close: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Per coin: lean against the crowd's side as shown by perp funding."""
    sig = _funding_signal(p["funding"], p).reindex(close.index).fillna(0.0)
    sig = sig.where(live_mask(close, p["warmup"]), 0.0)
    if p.get("long_only"):
        sig = sig.clip(lower=0.0)
    return sig * p["max_gross"] / close.shape[1]


def funding_basket(close: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Whole basket long or short against market-wide average funding."""
    mkt = p["funding"].mean(axis=1).to_frame("mkt")
    side = _funding_signal(mkt, p)["mkt"].reindex(close.index).fillna(0.0)
    if p.get("long_only"):
        side = side.clip(lower=0.0)
    return equal_slots(live_mask(close, p["warmup"])).mul(side * p["max_gross"], axis=0)
