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
