"""Per-coin time-series trend with volatility sizing (Roostoo hackathon).

Self-contained on purpose (pandas/numpy only, no `common/` imports): the
live bot imports this same module, so backtest and live share one signal.

Signal, per coin, on hourly closes:
  z_L   = log(close / close L hours ago) / (hourly_vol * sqrt(L))
  score = mean over lookbacks of clip(z_L / z_scale, -1, 1)   in [-1, 1]
  |score| < min_score -> flat. Positive -> long, negative -> short.

Sizing: each coin owns a fixed slot of max_gross / N_coins, scaled by
|score| and shrunk for coins more volatile than `vol_ref` (annualized):
  w = score * (max_gross / N) * min(1, vol_ref / coin_vol)
No renormalization across coins, so one coin's signal never resizes
another, and gross exposure falls when trends are weak. Gross is always
<= max_gross. 1x only: a Roostoo short locks USD collateral equal to its
notional, so long + short gross can never exceed equity.
"""

import numpy as np
import pandas as pd

HOURS_PER_YEAR = 365 * 24


def trend_score(close: pd.DataFrame, lookbacks: list[int], vol_window: int,
                z_scale: float) -> pd.DataFrame:
    log_close = np.log(close)
    hourly_vol = log_close.diff().rolling(vol_window, min_periods=vol_window).std()
    parts = []
    for lb in lookbacks:
        z = (log_close - log_close.shift(lb)) / (hourly_vol * np.sqrt(lb))
        parts.append((z / z_scale).clip(-1.0, 1.0))
    return sum(parts) / len(parts)


def target_weights(close: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Target portfolio weights per hour (fraction of equity, short < 0).

    Row t uses only closes up to and including bar t. The caller must hold
    these weights from bar t+1 onward (one-bar lag), never on bar t itself.
    """
    score = trend_score(close, cfg["lookbacks"], cfg["vol_window"], cfg["z_scale"])
    score = score.where(score.abs() >= cfg["min_score"], 0.0)
    if cfg.get("long_only"):
        score = score.clip(lower=0.0)

    ann_vol = np.log(close).diff().rolling(cfg["vol_window"]).std() * np.sqrt(HOURS_PER_YEAR)
    vol_cap = (cfg["vol_ref"] / ann_vol).clip(upper=1.0)
    slot = cfg["max_gross"] / close.shape[1]
    return (score * slot * vol_cap).fillna(0.0)


def drawdown_scale(equity: float, peak: float, cfg: dict) -> float:
    """Exposure multiplier from the book's own drawdown (Calmar protection).

    Below -dd_limit from the trailing peak, cut exposure to dd_factor.
    dd_limit <= 0 disables the brake.
    """
    if cfg.get("dd_limit", 0) <= 0 or peak <= 0:
        return 1.0
    return cfg["dd_factor"] if equity / peak - 1.0 < -cfg["dd_limit"] else 1.0
