# $100/day research decision — November 8, 2026

**No configuration qualified as dependable $100/day income.** This research ran
19,392 scenario simulations covering 2,160 distinct market/timeframe/parameter
configurations across 16 rule families. These are research adaptations, not
2,160 independently sourced strategies or native Freqtrade backtests.

No production worker, wallet, entry authority, position control or existing
strategy parameter changed. There were no live or paper orders and no Grok calls.

## What was tested

- 720 configurations on hourly crypto, 720 on four-hour crypto, 720 on daily ETFs.
- Sixteen families: RSI2 dips, RSI14 recovery, Bollinger reentry, IBS reversal,
  Donchian breakout, EMA pullback/crossover, MACD turn, stochastic recovery,
  ADX breakout, Keltner breakout, CCI/MFI recovery, ATR trend flips, absolute
  oversold and drawdown/ROC recovery.
- Three entry thresholds, three initial ATR stops and five exit profiles per
  family. Initial exits used targets of 2/4/8 ATR with 24/72/168-hour holds.
  A second exploratory iteration added no-fixed-target exits with a 3-ATR
  closing-price trail and 720/2,160-hour maximum holds. Daily ETFs interpret
  those holds as 1/3/7/30/90 trading sessions.
- Capital: 10k, 25k, 50k and 100k USDT/USD scenarios, no borrowing or leverage.
  All configurations were screened at 10k; development-frozen finalists were
  separately checked at all four capital levels. The latest crypto snapshot
  evaluated the whole warm-ready grid at all four levels.
- Crypto fees of 0.4%/0.8% per side; ETF proxy fees of 0.05%/0.1% per side;
  0.05% slippage per side. These are modeled costs, not verified account fees.
  Operating cost is an illustrative $2 per calendar day, not a measured bill.

The strategy portfolio uses one wallet per scenario, six maximum simultaneous
positions, approximately one-sixth equity per entry, and a preceding-candle
1% quote-volume sizing assumption. Running more strategies does not multiply
the capital available to that wallet. Large-capital results remain conditional
on the modeled liquidity assumptions.

## Data and separation

Historical crypto inputs are 14 hourly Binance USDT files pinned to commit
`553525cf23aa1af1155aa64d071ab1ddd5f940c1` of
DigiTuccar/HistoricalDataForTradeBacktest. Git blob identities were checked.
Four-hour bars use complete, aligned groups of four actual hourly bars.
Missing source candles are not replaced with fabricated prices.

Historical equity inputs are public Yahoo daily OHLC for SPY, QQQ, IWM, DIA,
XLK, XLF, XLE, XLI, XLP, XLU, GLD and TLT. These are fixed surviving ETFs,
not a point-in-time stock universe, SIP feed or equity-scalper execution replay.
Dividends are not credited; fractional shares are modeled.

Development: 2022–2023. Validation: 2024. Assessment: January–April 2025.
Finalist lists used development only and were saved before later-period runs.
All these periods have appeared in earlier research: do not call them pristine
unseen holdouts. Both iterations and their parameter choices are retained.
The second iteration was informed by the first and is explicitly exploratory.

Fresh Kraken USD data was captured on November 8, 2026 at approximately 23:35 UTC.
The 29 public Kraken requests captured 14 hourly and 14 four-hour markets plus
server time. Twelve public Yahoo historical requests supplied the ETF proxy.
Unfinished candles were excluded.

| Fresh Kraken evaluation | Complete days | Warm-ready configurations | Scenario runs |
|---|---:|---:|---:|
| Hourly: Oct 19–Nov 8, 2026 | 20 | 675 | 5,400 |
| Four-hour: Aug 15–Nov 8, 2026 | 85 | 675 | 5,400 |

Each timeframe explicitly excluded 45 absolute-oversold configurations whose
1,200-bar SMA could not warm from the API's 720-bar window. History requirements
were not shortened to force trades. The hourly/four-hour windows differ in length.
Current-window leaders were selected after viewing current-window results, and
their subsequent historical diagnostics do not constitute independent validation.

## What the promising numbers actually mean

At 10k capital, 0.4% crypto fees per side and modeled slippage/overhead:

| Configuration and window | Average net/day | Regular closes | Win rate |
|---|---:|---:|---:|
| ADX breakout, 4h, 2024 | +29.64 | 105 | 38.1% |
| Same ADX configuration, Jan–Apr 2025 | -14.65 | 24 | 8.3% |
| EMA50/100 crossover, 4h, Aug–Nov 2026 | +25.83 | 16 | 93.8% |
| Same EMA configuration, 2022–2023 | -7.07 | 184 | 37.0% |
| Same EMA configuration, 2024 | -1.64 | 106 | 37.7% |
| Same EMA configuration, Jan–Apr 2025 | -11.88 | 35 | 40.0% |

ADX configuration: trigger 35, initial stop 5 ATR, target 8 ATR, maximum hold
168 hours. EMA configuration: fast EMA50 crossing above EMA100, initial stop
3 ATR, target 8 ATR, maximum hold 168 hours. EMA exit signal occurs when the
fast EMA falls below EMA100. Neither is a source-faithful author reproduction.

The recent EMA result is 15 wins / 16 regular closes, +2,195.37 net over
85 calendar days. It had 69 negative net marked days after overhead, only six
days at or above $100, and a worst marked day of -554.55. A high trade win rate
does not establish daily income. Historical crypto and current crypto use
different venues and quote currencies, which also limits direct comparisons.

A different recent EMA20/100 trailing configuration averaged +214.51/day on a
100k modeled wallet over the recent 85-day window. It lost in historical
development and January–April 2025. That is not a justified reason to fund a
larger account. No frozen finalist met a $100 average across all historical
periods in any tested capital/fee scenario, and no every-day income claim passed.

## Validation and limits

41 focused tests passed. They include ten seeded comparisons against the existing
independent reference wallet, next-open timing, round-trip costs, cash
reconciliation, stop/target ambiguity, next-bar trailing stops, weekend equity
marking, rejection of malformed captures and removal of unfinished candles.
Signal-prefix checks cover all sixteen families. All 2,592 first-iteration
fixed-exit scenario rows reproduced exactly under the extended implementation.

The model executes completed-bar signals at the next available open, gives stops
priority when both stop and target are touched, charges both sides, and liquidates
remaining inventory at the final mark with modeled exit costs. Forced terminal
closes affect PnL but are excluded from the regular closed-trade win rate.

No native Freqtrade or broker backtests were run in this study. There is no
order-book queue, venue precision, actual fill, latency, halt, tax or financing
simulation. Cash withdrawals are not modeled. Operating overhead is subtracted
at period end rather than withdrawn from trading capital daily. Figures are
marked-to-market calendar-day averages, not guaranteed cash payouts or assessed
future returns. No risk-adjusted benchmark outperformance was established.

## Reproduction

Use an isolated Python 3.13 research environment; do not replace the desktop
environment. Install `requirements-income-research.txt`. Run focused tests with
`--confcutdir=tests/strategy_lab` to avoid unrelated desktop fixtures.

Main entry points:

- `python -m app.strategy_lab.daily_income_lab --root NEW_DIR --prepare-history PINNED_HOURLY_DIR --stage development`
- Repeat with `--stage validation` and `--stage assessment` after development
  freezes the finalist list. Existing results are deliberately not overwritten.
- `python -m app.strategy_lab.equity_income_study --root NEW_DIR --prepare --reuse-data ARCHIVED_EQUITY_DIR --stage development`
- `current_income_snapshot`, `current_income_grid`, `income_capital_review` and
  `income_current_leaders` contain the current-data and diagnostic entry points.

Raw captures, protocols, checksums, scenario results and daily finalist ledgers
are retained in the separate research evidence archive. Protocols bind the exact
implementation and inputs; prepare fresh directories when changing code or
relocating inputs. Public snapshot commands request the latest API window and
will differ on a later date. Exact archived-snapshot verification must use the
retained raw responses. Original first-iteration implementations are preserved
under `docs/research-snapshots/income-v1/` for provenance.

Decision: retain the research evidence and current leaders for further study;
make no production parameter change or $100/day claim from these results.
