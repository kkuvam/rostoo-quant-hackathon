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
uv run python fetch_data.py --timeframe 1h   # also 1m
uv run python vector_sweep.py
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

## Exposure variants (2026-10-02)
Same funding_contra 72h long-only, limit costs, band 0.0025. Weights x mult,
then gross capped at 0.95. Median 14-day composite (all from 2020-07 /
2020-23 / 2024-26Q1 / 2026-04+), 2022 total, Jan 2021 total:
| variant | all | 2020-23 | IS | OOS | avg gross | 2022 | Jan 2021 |
|---|---|---|---|---|---|---|---|
| **base 1/21** | **3.77** | 3.97 | **2.91** | 4.17 | 0.38 | -32% | +10% |
| slot x1.5 | 3.61 | 4.18 | 2.43 | 3.95 | 0.52 | -50% | +15% |
| slot x2 | 3.37 | 3.89 | 1.99 | 4.14 | 0.59 | -61% | +20% |
| slot x3 | 2.92 | 3.32 | 1.58 | 3.65 | 0.66 | -68% | +31% |
| live coins (max_gross/n_live) | 3.76 | 4.10 | 2.91 | 4.17 | 0.40 | -38% | +12% |
| scale 0.005% | 3.47 | 4.00 | 2.11 | 3.83 | 0.48 | -44% | +10% |
| neutral 0.015% | 3.31 | 4.52 | 1.55 | 4.10 | 0.60 | -54% | +24% |
| neutral 0.02% | 2.76 | 4.56 | 0.50 | 4.40 | 0.73 | -63% | +41% |
| ew hold | 1.76 | 3.40 | -0.13 | 4.40 | 0.95 | -70% | +156% |

14-day return tails (all windows from 2020-07): base p50 +0.5%, p75 +3.1%,
p90 +7.3%, 16% of windows > +5%. slot x1.5: +0.7% / +4.3% / +10.1% / 22%.
slot x2: +0.7% / +4.7% / +11.4% / 24%. neutral 0.015%: +1.0% / +5.0% /
+10.9% / 25%. ew hold: +0.9% / +10.6% / +21.1% / 38%.
Read: more exposure moves the strategy toward EW hold. Composite falls
in every step and 2022 losses grow, the upper tail rises. Live-coin sizing
equals base today (all 21 coins have full history), its edge is 2020-only.
The neutral shifts change the signal (coins at the default rate get a
position), fall apart in 2024-26Q1 and were outside the robustness grid.
All of 2020-26 was already seen, so this is in-sample selection.
Decision: base 1/21 stays the default. slot x1.5 is the one knob to turn
if the top-20 return gate needs more upside (p90 +7% -> +10%).

## Upside variants for the return gate (2026-10-02)
Same base, band 0.0025, limit costs, gross capped at 0.95. Composite = median
14-day (all from 2020-07 / IS / OOS), 14-day return tails over all windows
from 2020-07, then month/year totals.
- top k: equal max_gross/k in the k coins with the lowest 72h funding (below neutral).
- trend tilt: slot x mult when the coin is above its 30d (720h) or 7d EMA.
- fear overlay: slot x (1 + extra * clip((0.01% - market 72h funding) / 0.01%, 0, 1)).

| variant | comp all | IS | OOS | p50 | p90 | >5% | >10% | gross | Jan21 | 2022 | Sep20 | Jun26 | Sep26 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| base | 3.77 | 2.91 | 4.17 | +0.5% | +7.3% | 17% | 5% | 0.38 | +10% | -32% | +1% | -15% | +14% |
| slot x1.5 | 3.61 | 2.43 | 3.95 | +0.7% | +10.1% | 22% | 10% | 0.52 | +15% | -50% | +1% | -19% | +20% |
| top 5 | 2.43 | 0.75 | 6.22 | +1.1% | +14.8% | 32% | 18% | 0.77 | +49% | -78% | +3% | -6% | +21% |
| top 8 | 2.50 | 0.92 | 4.27 | +0.9% | +13.3% | 29% | 15% | 0.73 | +29% | -75% | +2% | -14% | +24% |
| trend tilt 30d x2 | 2.50 | 1.63 | 4.32 | +0.5% | +9.4% | 20% | 9% | 0.48 | +19% | -43% | -1% | -18% | +23% |
| trend tilt 30d x3 | 1.93 | 1.30 | 5.00 | +0.4% | +11.3% | 23% | 12% | 0.55 | +29% | -50% | -3% | -18% | +29% |
| trend tilt 7d x2 | 2.11 | 1.58 | 2.76 | +0.3% | +10.1% | 20% | 10% | 0.49 | +18% | -52% | -1% | -19% | +19% |
| **fear overlay +1** | **3.89** | 2.42 | 4.14 | +0.5% | +10.2% | 23% | 10% | 0.54 | +10% | -56% | +2% | -19% | +21% |
| fear overlay +2 | 3.49 | 2.01 | 3.91 | +0.5% | +11.0% | 24% | 12% | 0.59 | +10% | -62% | +4% | -19% | +22% |
| ew hold | 1.76 | -0.13 | 4.40 | +0.9% | +21.1% | 38% | 26% | 0.95 | +156% | -70% | -17% | -17% | +37% |

Read: no free upside. Every variant that lifts p90 costs IS composite and
long-bear losses. Fear overlay +1 keeps the best whole-period composite
(3.89) with the same tail gain as slot x1.5. At today's market funding
(0.005%/8h) it sizes at 1.5x. Top 5/8 is the aggressive choice: p90
+13-15%, but IS composite < 1 and 2022 -75%+. Trend tilts lose to both.

## 2026-10-02 Cycle analogue: post-peak capitulation (2022)

Macro view: Oct 2026 is 12 months after the Oct 2025 high, which matches Nov 2018.
Binance perp funding starts Sep 2019, so 2018 cannot be tested with this signal.
Nearest analogue in our data: Nov 2021 high, quiet Oct 2022, FTX capitulation
Nov 2022. Same settings as above, limit costs. 14-day windows start daily
2022-10-01 to 2022-12-17.

| span | base | fear overlay +1 | top 8 | ew hold | btc hold |
|---|---|---|---|---|---|
| Oct 4-17 2022 | -0.6% | -1.2% | -2.4% | -3.0% | -1.2% |
| Nov 6-20 2022 (FTX) | -15.4% | -23.9% | -34.0% | -30.5% | -23.7% |
| Oct 2022 | +4.4% | +7.1% | +6.2% | +7.5% | +5.6% |
| Nov 2022 | -5.6% | -9.9% | -23.3% | -17.7% | -17.0% |
| Oct-Dec 2022 | -8.6% | -14.0% | -30.3% | -25.3% | -14.4% |
| Jun 2022 (LUNA/3AC) | -22.6% | -31.6% | -31.0% | -30.6% | -39.6% |
| 14d window median | -1.0% | -2.2% | -2.4% | -4.3% | |
| 14d window worst | -13.9% | -21.6% | -31.8% | -28.0% | |
| 14d window best | +11.4% | +19.1% | +19.2% | +21.9% | |

Read: in a capitulation the base sizing loses least and beats BTC hold. The
fear overlay scales up as funding turns negative in the crash, so it adds
about half again to the loss. Top 8 loses as much as the basket. No variant
makes money in the crash fortnight, so return rank there depends on the
rest of the field losing more.
