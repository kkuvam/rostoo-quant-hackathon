# roostoo_trend

Strategy for the Roostoo hackathon (live round Oct 4-17 2026, $100k, 1x long
and short, no leverage, taker 0.1% / maker 0.05%, shorts 0.1%). Score:
return gate (top 20 per region), then 0.4*Sortino + 0.3*Sharpe + 0.3*Calmar.

## Logic (logic.py)
Per-coin time-series trend on hourly Binance closes, 21 coins that Roostoo
lists (Roostoo prices match Binance within ~2 bps, checked 2026-10-01).
Score = mean over lookbacks of clip(vol-normalized log return, -1, 1).
Positive -> long, negative -> short. Each coin has a fixed slot
max_gross/N, scaled by |score| and shrunk if the coin's vol > vol_ref.
Rebalance once a day on the close of the 00:00 UTC hourly bar (Binance
labels bars by open time, so the live bot trades at ~01:00 UTC, after that
bar closes, never on a partial bar). Skip trades smaller than `band`, halve
exposure when equity is >4% below its 7-day peak. Live warm-up needs
~888 hourly bars (720h lookback + 168h vol window): one 1000-bar klines call.

## Backtest (vector_sweep.py)
Plain hourly weights simulation, weights lagged one bar, 0.13% per side
(0.1% fee + 3 bps slippage) on every weight change. Scored on rolling
14-day windows, step 1 day. In-sample 2024-01-01 to 2026-03-31,
out-of-sample 2026-04-01 to 2026-10-01.
Self-contained folder (no imports from the parent repo), so it can move to
its own repo. Data and reports land in `data/` and `reports/` here (gitignored).
```
uv run python roostoo_hackathon/fetch_data.py --timeframe 1h   # also 1m
uv run python roostoo_hackathon/vector_sweep.py
```
Data: Binance bulk archive (data.binance.vision), 2024-01-01 to yesterday,
1h and 1m. Archive 1h bars match the Binance API bars exactly (BTC, checked
2026-10-01).

## Sweep 1 (2026-10-01): 1-14 day lookbacks, renormalized vol sizing: negative
48 combos (lookbacks 1/3/7d or 3/7/14d, rebalance 4/12/24h, min_score,
vol target, dd brake). Every combo had a negative median 14-day return
in-sample. Best IS median composite -0.05. Cause is cost: the 1/3/7d
version made +102% IS before costs and -5% after (book turned over ~300x
a year). Sizing bug found after: vol-target weights always hit the cap
and were renormalized to ~0.93 gross, so the vol-target knob was inert.

## Sweep 2 (2026-10-01): 7-30 day lookbacks, fixed per-coin slot
| lookbacks | band | dd | IS med 14d | IS p10 | IS med MDD | IS comp | IS total | IS turn/yr | % windows >=10 trade days | OOS med 14d | OOS comp | OOS total |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 7/14/30d | 0.02 | 4% | -0.2% | -3.4% | 3.9% | -0.71 | +42% | 21x | 57% | -0.4% | -2.06 | +10.2% |
| 7/14/30d | 0.02 | off | -0.2% | -3.6% | 4.1% | -0.83 | +49% | 21x | 63% | -0.4% | -1.60 | +11.9% |
| **7/14/30d** | **0.01** | **4%** | **-0.2%** | **-3.7%** | **4.3%** | **-0.97** | **+47%** | **33x** | **95%** | **-0.1%** | **-0.46** | **+12.9%** |
| 14/30d | 0.02 | off | -0.3% | -3.7% | 4.1% | -1.07 | +52% | 18x | 57% | -0.1% | -0.17 | +12.9% |
| 7/14/30d | 0.01 | off | -0.3% | -4.1% | 4.5% | -1.15 | +37% | 33x | 96% | -0.3% | -0.81 | +12.7% |

Benchmarks: BTC hold IS med 14d +0.3%, comp 1.05, total +61%; OOS med
+1.5%, comp 3.71, total +24%. Equal-weight hold OOS med +2.1%, comp 4.00.

Chosen (bold): 7/14/30d, band 0.01, 4% brake. The 0.02 band trades on
>=10 days in only ~60% of 14-day windows, which fails the contest's
"trades every day" log check, so band 0.01 wins. OOS was viewed before
this choice (sweep 1 diagnostics showed OOS for the long-lookback family).

## Honest read
Positive over the long run (+47% IS / +13% OOS at ~0.32-0.39 average
gross, ~4% median 14-day drawdown), but the median 14-day window is flat
to slightly negative. The edge comes from a few strong trend months
(positive skew). On one 14-day draw the most likely outcome is roughly
flat, which will not pass the top-20 return gate unless the market trends
during the contest. Buy-and-hold beat it on composite in both periods.

## Candidate comparison (2026-10-01, compare.py, candidates.py)
Same simulator and 14-day scoring, daily rebalance, 0.13%/side, no dd brake.
Median daily-basis composite, in-sample (to 2026-03-31) / out-of-sample:
- ew_hold (benchmark, always long all coins): -0.09 / +3.77
- trend (logic.py, 7/14/30d): -1.15 / -0.96
- vol_beta (basket long/short on 7d EMA, vol-targeted): best +1.65 / -5.58 (fails OOS)
- xs_momentum (top/bottom k by 3-7d return, regime side): best +0.36 / -1.16
- xs_reversal (buy 1-3d losers in up regime): all < -4 IS
- donchian (7d/20d breakout per coin): best IS -1.29 (20d L/S), OOS +3.36: inconsistent
- zscore_mr (fade 1-3d z-score, 4h rebalance): all negative, turnover kills it
- **funding_contra** (per coin, long-only, lean against 3-day avg perp funding,
  neutral 0.01%/8h, full position 0.01% below it): **+2.68 / +4.07**,
  positive in 56% / 62% of windows, median MDD 5.6% / 4.9%, 89% / 100% of
  windows trade on >= 10 days. The long/short version: +1.49 / +4.07, but its
  shorts lose (IS total -29%).
- funding_basket (whole basket against market-average funding): +2.50 / +5.02,
  but trades on >= 10 days in only 17% / 31% of windows (fails the log check).

Robustness of funding_contra (long-only), 18 neighbours (funding window
48/72/120h x neutral 0.005%/0.01% x scale 0.005-0.02%), median composite
by year: 2024 +5.4 to +10.7 (EW hold +2.3), 2025 -1.0 to +1.4 (EW hold -1.7),
2026 +0.5 to +5.6 (EW hold +0.9). Beats EW hold in nearly every cell and
year. It is smarter beta, not a hedge: 2025 total -6% vs EW hold -25%.

Blend funding_contra + trend sleeve (to show shorts): every blend scores
lower. 80/20: IS +1.18 / OOS +3.29, shorts in 68% / 23% of windows.

Open question for the contest: the rules list "long, sell, short, and
close". If shorts are mandatory, use the 80/20 blend; if not, pure funding.
Funding data: `fetch_data.py --funding` (Binance USD-M public endpoint).
The live bot must pull funding from Binance too (Roostoo has none).

## Limit-order costs (2026-10-02)
Costs are now per side of the book (config.yaml `costs`): limit = 0.05% on
long-side trades, 0.10% on short-side trades (Roostoo shorts pay 0.1% for
any order type); market = 0.13% both. `compare.py --market` reproduces the
earlier numbers exactly. Limit results, median composite IS / OOS:
- funding_contra 72h long-only: +2.96 / +4.47 (was +2.68 / +4.07), still #1
  among strategies that trade on >= 10 days (89% / 100% of windows).
- funding_contra 72h long/short: +1.65 / +4.47 (shorts still lose IS).
- xs_momentum 72h k=5: +0.90 / +0.87 (was +0.27 / -0.15): the only
  price-only idea that turns positive in both periods, but weak.
- trend: -0.89 / -0.38. vol_beta 168h L/S: +2.07 / -4.87 (still fails OOS).
- zscore_mr 24h long-only: +1.70 / +0.91 (was -1.61 / -3.93), but total
  return IS -32%: cheaper costs, still a loser over time.
Cheaper fees help the high-turnover ideas most, but the ranking at the top
does not change.

Limit fill check (tmp script on 1m data, 2025-01 to 2026-09, daily 01:00 UTC
order at the last 1m close, filled only if price trades strictly through):
BTC/ETH/SOL/DOGE/ZEC/HBAR fill 88-94% within 5 min, 95-98% within 60 min.
Unfilled orders saw price move 60-95 bps away within the hour, so a
"limit, then market after 60 min" rule costs ~0.05% + ~0.03% = ~0.08% on
average for long-side trades. Roostoo's mock matching may differ from Binance.

## Chosen entry and long history (2026-10-02)
User call: shorts not required -> funding_contra, 72h, long-only, limit
costs. Data backfilled to 2020-06 (`fetch_data.py --since 2020-06-01`, also
--funding); 15 of 21 coins exist in late 2020 (no SUI/WLD/ENA, NEAR/AAVE
from mid-Oct 2020). Slots stay max_gross/21, so gross was lower in 2020.

| Period | funding | EW hold | BTC hold |
|---|---|---|---|
| Sep 2020 | +0.6% (DD 4.3%) | -17.3% (DD 29.7%) | -8.6% |
| Oct 2020 | +0.7% (DD 2.2%) | -2.3% | +27.6% |
| Jan 2021 | +10.0% (DD 2.6%) | +152.6% | +15.6% |
| 2022 | -31.8% (DD 43.9%) | -69.0% | -64.4% |
| 2020-07 to 2026-09 | +123%, max DD 46.5% (2022-06) | +2170%, DD 77% | +780%, DD 77% |

Whole period, 14-day windows: funding median +0.48%, positive 60%, median
DD 3.9%, median composite +3.79 (EW +1.74, BTC +2.04), trades >= 10 days in
81% of windows, average gross 0.37. Monthly: captures 25% of EW's up months
and 32% of its down months. Jan 2021: market funding averaged 0.049%/8h
(5x neutral) so it held ~7% gross through a +150% month.
Character: a low-exposure, contrarian long book. Best risk-adjusted score
per window of anything tested, but it cannot keep up in euphoric rallies,
and it keeps buying through long bear markets (2022: -32%).
