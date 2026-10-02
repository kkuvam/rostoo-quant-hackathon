# Roostoo Quant Hackathon: Funding-Rate Contrarian Portfolio

A long-only, daily-rebalanced crypto portfolio over 21 Roostoo-listed coins.
Each coin's position size comes from one signal: how crowded leveraged traders
are on that coin, read from its perpetual-futures funding rate. The book holds
more of a coin when leveraged traders are bearish on it and little or none
when they are crowded long.

Live round: Oct 4-17 2026, $100k, spot long and short allowed, no leverage.
Fees: 0.05% maker (limit), 0.10% taker (market).

## 1. How the portfolio is curated

**Universe.** 21 coins that Roostoo lists and that also have a Binance
USD-M perpetual (needed for the funding signal):
BTC ETH SOL XRP BNB DOGE ADA AVAX LINK SUI NEAR UNI LTC DOT TRX ZEC ENA WLD AAVE
HBAR XLM. Roostoo's USD prices track Binance USDT pairs within ~2 bps (checked
live), so Binance history is a faithful proxy for backtesting.

**Signal (per coin, every hour).**
1. Fetch the coin's funding-rate history from Binance USD-M futures (public API).
2. Normalize every payment to a per-8-hour rate (some coins settle every 4h or 1h).
3. Average the last 72 hours.
4. Score = clip(-(avg - 0.01%) / 0.01%, 0, 1).
   - avg >= 0.01% per 8h (Binance's default, longs paying) -> 0, no position
   - avg = 0.005% -> 0.5, half a slot
   - avg <= 0% (shorts paying, fear) -> 1, full slot

**Position size.** Each coin has a fixed slot of 0.95 / 21 = 4.5% of equity,
multiplied by its score. Gross exposure therefore floats with market sentiment:
near zero in euphoria, up to the 95% cap only when every coin's funding is at
or below zero. Over 2020-2026 the average was 38%. A coin needs 720 hours of
price history before it can be bought, so fresh listings are skipped.

**Rebalance.** Once a day, right after the 00:00 UTC hourly bar closes
(~01:00 UTC), never on a partial bar. Trades smaller than 0.25% of equity are
skipped to avoid churn. A full exit is always allowed.

## 2. Why this strategy (the thinking)

A perpetual future has no expiry. To keep its price near spot, the side that
is crowded pays the other side a funding fee every 8 hours. Funding is
therefore a direct, public measure of leveraged positioning:

- High positive funding means many traders are long with leverage. Crowded
  leveraged longs tend to be liquidated in cascades, so forward returns are
  poor.
- Zero or negative funding means traders are short or scared. Crowded shorts
  tend to be squeezed, so forward returns are better.

This is an economic mechanism, not a chart pattern, which is why we preferred
it to price-only signals. Price-only signals also failed our tests (section 4).
We kept it long-only: the short side of the same signal lost money in-sample,
and shorts on Roostoo cost 0.1% for any order type.

## 3. Implementation

| File | Role |
|---|---|
| `fetch_data.py` | Downloads Binance spot klines (1h, 1m) from the bulk archive `data.binance.vision`, and funding history from `fapi.binance.com`. Resumable, backfills on request. Data lands in `data/` (gitignored). |
| `candidates.py` | Every strategy we tested, as pure functions `(close, params) -> target weights`. The submitted strategy is `funding_contra` (long_only, 72h). |
| `vector_sweep.py` | The backtest engine and contest scoring (section 4). |
| `compare.py` | Runs every candidate through identical costs and scoring, `--market` for taker costs. |
| `logic.py`, `config.yaml` | First strategy (multi-horizon trend), kept for the record. Config holds universe, costs and periods. |
| `notes.md` | Dated research log of every sweep and decision, including the failures. |

```
uv sync
uv run python fetch_data.py --timeframe 1h --since 2020-06-01
uv run python fetch_data.py --funding --since 2020-06-01
uv run python compare.py funding_contra
```

## 4. Backtesting

**Engine.** A plain hourly weights simulation, written for a multi-asset book
with fractional weights:

- Weights computed on bar t's close are held from bar t+1 (one-bar lag, no
  look-ahead).
- A funding payment stamped at time T is placed on the bar that opens at T,
  so the strategy only sees it after it is public.
- Trades only on rebalance hours. Every weight change pays the fee for its
  side (section 5).
- Equity is marked hourly on Binance closes.

**Scoring like the contest.** The contest ranks a 14-day run, so we score
every rolling 14-day window (step 1 day), not one long equity curve. Each
window gets the judges' composite 0.4 x Sortino + 0.3 x Sharpe + 0.3 x Calmar,
annualized on daily returns. We report the median and the spread across
windows, plus the share of windows that trade on at least 10 distinct days
(the activity rule).

**Periods.** Parameters were chosen on 2024-01 to 2026-03 (in-sample). We then
checked 2026-04 to 2026-09 (out-of-sample), and afterwards backfilled
2020-07 to 2023-12, which the strategy had never seen.

**Results (limit-order costs, 0.25% trade band).** Median 14-day composite:

| Period | Funding contrarian | Equal-weight hold of all 21 | Total return (strategy) | Max drawdown (strategy) |
|---|---|---|---|---|
| 2020-07 to 2023-12 (unseen) | **3.97** | 3.40 | +49% | 46.6% |
| 2024-01 to 2026-03 (in-sample) | **2.91** | -0.13 | +11% | 46.8% |
| 2026-04 to 2026-09 (out-of-sample) | 4.17 | 4.40 | +28% | 20.8% |
| Whole 2020-07 to 2026-09 | **3.77** | 1.76 | +112% | 46.8% |

Whole period, per 14-day window: median return +0.5%, 10th percentile -5.5%,
90th percentile +7.3%, 60% of windows positive, median drawdown 4.0%, trades on
>= 10 days in 87% of windows, book turnover ~26x per year.

Behaviour in different markets:

| Month or year | Strategy | Equal-weight hold | BTC hold |
|---|---|---|---|
| Sep 2020 (sell-off) | +0.6% | -17% | -8.6% |
| Jan 2021 (euphoric rally) | +9.8% | +156% | +15.6% |
| 2022 (year-long bear) | -32% | -70% | -64% |
| Jun 2026 (sharp crash) | -15% | -17% | -20% |
| Sep 2026 (broad alt rally) | +14% | +37% | +6% |

**Robustness.** 18 neighbouring settings (funding window 48/72/120h, neutral
0.005%/0.01%, scale 0.005-0.02%) all beat equal-weight hold on median
composite in nearly every year. The result does not hinge on one lucky
parameter.

**What we rejected, and why.** Same engine, same costs (limit), median
14-day composite in-sample / out-of-sample:

| Strategy | IS | OOS | Verdict |
|---|---|---|---|
| Multi-horizon trend (7/14/30d), long/short | -0.89 | -0.38 | Profitable long-run, but the median fortnight is negative |
| Basket trend with volatility targeting | +2.07 | -4.87 | Overfit: broke out-of-sample |
| Cross-sectional momentum (top/bottom 5, 72h) | +0.90 | +0.87 | Weak, 380x/yr turnover |
| Z-score mean reversion, long-only | +1.70 | +0.91 | Lost 32% in-sample |
| Short-term reversal | < -3 | < -3 | Loses in every setting |
| Donchian breakout | -1.25 | +3.51 | Inconsistent across periods |
| Funding contrarian, long/short | +1.65 | +4.47 | The short side loses money |
| Funding on the whole basket | +2.76 | +5.12 | Trades on >= 10 days in only 11-31% of windows |
| 8 earlier intraday BTC strategies | n/a | n/a | Lost 80-100% to fees |

**Sizing variants for the return gate.** Only the top 20 by return reach the
composite ranking, so we tested ways to add upside (full table in
`notes.md`). A "fear overlay" that scales the whole book up to 2x when
market-wide funding falls toward zero kept the best whole-period composite
(3.89) and raised the 90th-percentile fortnight from +7.3% to +10.2%. It
costs a deeper loss in a year-long bear (-56% in 2022). Concentrating on the
5-8 lowest-funding coins lifts the upside further but scores below 1
in-sample. Bigger fixed slots and trend tilts were dominated.

## 5. Transaction fees (maker vs taker)

Fees are charged per side of every trade in the backtest:

| Mode | Long-side cost | Basis |
|---|---|---|
| Limit (default) | 0.05% | Roostoo maker fee |
| Market | 0.13% | 0.10% taker fee + 3 bps slippage |

We checked limit fills on Binance 1-minute data: a daily buy or sell limit
at the last price fills 88-94% of the time within 5 minutes and 95-98% within
60 minutes. Roostoo matches a limit order when the market reaches its price,
which is the rule we tested. The engine falls back to a market order after
60 minutes, so the realistic blended cost is about 0.08% per trade.

The strategy holds up under taker costs too (median composite +2.68 in-sample
/ +4.07 out-of-sample at 0.13%). With turnover around 26x a year, each extra
0.05% of fee costs about 1.3% a year, so the edge does not depend on cheap
fills. This is also why we rejected high-turnover ideas: an intraday version
of any price signal lost most of its edge to fees.

## 6. Risk management

- **No leverage, long-only.** No liquidation risk, no short squeeze risk, no
  0.1% short fee.
- **Gross cap 95%.** At least 5% of the book is always cash, for fees and
  rounding.
- **Per-coin cap 4.5%.** No single coin can hurt the book much. Positions are
  spread over 21 coins.
- **Exposure shrinks automatically when the market is crowded.** In euphoric
  markets (high funding) the book holds little, which is when crashes from
  liquidation cascades are most likely. In Jan 2021 it held ~7% of equity.
- **Trade band.** Changes below 0.25% of equity are skipped. This limits fee
  drag and noise trading.
- **Warm-up.** No coin is traded before 720 hours of history.
- **Execution risk.** Limit orders first, market fallback after 60 minutes,
  order amounts rounded to Roostoo's precision and checked against its
  minimum order value.
- **Data risk.** If the funding fetch fails, the bot keeps yesterday's
  positions and retries; one stale day changes the signal very little.

**Known weaknesses.** The strategy is smarter beta, not a hedge. It keeps
buying through long bear markets: its two worst drawdowns were -46.6% (May
2021 to Jun 2022) and -46.8% (Oct 2025 to Feb 2026), against roughly -75% for
the equal-weight basket. Over a 14-day run the median drawdown is 4%, but
the tail is real. It does not escape a sudden
one-month crash (Jun 2026: -15% vs -17% for the market), and it lags strong
rallies because it holds little when funding is high.

## 7. Trading engine (in progress)

The live bot is being built. Design:

- **Schedule.** Runs hourly. At ~01:00 UTC each day it computes target weights
  and rebalances. Other hours it only manages open orders and logs.
- **State.** Read from Roostoo every run (balances, open orders), never from a
  local file, so a restart cannot desync the book.
- **Orders.** Signed Roostoo v3 REST calls (HMAC-SHA256 over sorted params).
  Sells first to free cash, then buys. Limit orders at the last price,
  cancelled and replaced by market orders after 60 minutes.
- **Activity rule.** If no rebalance trade passes the band on a given day, the
  bot places one small trade so the log shows activity every day.
- **Data.** Funding from the public Binance futures API, prices from the
  Roostoo ticker.
- **Hosting.** AWS EC2 in a non-US region (Binance blocks US IPs), under
  systemd with automatic restart, logs to file.
- **Secrets.** API keys in a gitignored `.env`, never committed.

## 8. Honest limitations

- The return gate is the main risk: the median 14-day return is +0.5%, so the
  strategy needs a ranging or falling market, or the fear overlay, to rank in
  the top 20.
- All of 2020-2026 has now been viewed, so the sizing-variant choice is
  in-sample.
- Roostoo's mock matching engine is not Binance, so live fills can differ
  from the 1-minute fill test.
