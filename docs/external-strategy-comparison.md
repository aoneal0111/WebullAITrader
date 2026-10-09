# External strategy comparison: first batch

Reviewed 2026-10-09. This is an implementation and evidence audit of five
projects, followed by proposed Atlas experiments. No external historical
profitability result was reproduced. Synthetic checks establish specific
backtester behavior, not a trading edge. No Atlas trading rule was changed.

Atlas baseline: local commit `c4235160539e1de12e248be710fce7d14a293b23`,
tree `a11e5a927ca9db8e5aede96a873121b8c4aa3183`, matching the previously
validated Windows coverage-replay change at `d1e09ea`.

## Implementations inspected

| Project | Evidence inspected | Atlas relevance | Decision |
| --- | --- | --- | --- |
| [pysystemtrade](https://github.com/pst-group/pysystemtrade) | Established since 2015; author describes own backtesting and production futures system. Read volatility-normalized EWMAC and smoothed range-position breakout rules. | Compare strength across instruments and time horizons while sizing risk consistently. | Good design reference; daily futures forecasts are not an intraday equity strategy. |
| [NostalgiaForInfinity](https://github.com/iterativv/NostalgiaForInfinity) | Published backtests, data repository, and all-years testing script. Read legacy Next strategy's tagged quick/long exit routing and profit-conditioned exits. | Distinct entry families can have distinct exit policies rather than sharing one universal exit. | Reference architecture; legacy code is not proof of current-version performance. |
| [Concretum ORB notebook](https://concretumgroup.com/backtesting-the-opening-range-breakout-orb-strategy-using-polygon-io/) | Public research-linked Python implementation and performance tables. Read entry, stop, sizing, bar ordering, and cost code. | A simple early-session benchmark against later HOD entries. | Useful benchmark after matching costs, data and execution assumptions. |
| [sam-bateman/trading-orb](https://github.com/sam-bateman/trading-orb) | Author reports long-history and walk-forward results. Read the actual maximum-history runner and its backtester; ran four synthetic checks. | Volume-confirmed morning breakouts and restricted entry windows. | Do not adopt the engine or its headline metrics without repair and reproduction. |
| [Qlib LightGBM + TopkDropout](https://github.com/microsoft/qlib/tree/main/examples/benchmarks/LightGBM) | Actual stock-ranking strategy, chronological train/validation/test configuration, cost settings and published repeated-run benchmarks. | Rank competing opportunities and control replacement/turnover instead of entering every signal. | Method reference; daily Chinese equity results do not establish a US intraday edge. |

### Versioned code references

- pysystemtrade commit `bbe29e19dab73271570c94b4ee7e2f182d4b9ef1`:
  `systems/provided/rules/ewmac.py` (blob `0a2f79b107ac1038697db9bc77a0a460f5a26f15`)
  and `systems/provided/rules/breakout.py`
  (blob `7957fc51323b41cece4ed1273bfbf0dd33f654c8`). EWMAC divides the
  moving-average difference by estimated daily price volatility; breakout
  expresses position within a rolling range and smooths the forecast.
- NFI commit `918b34e3730496839a9aac670fee5c5b05039b84`:
  `legacy/NostalgiaForInfinityNext.py`
  (blob `958e22909d587b38cc05f9020e850dcf5b6ea9aa`) and
  `tests/backtests/backtesting-all-years-all-pairs.sh`
  (blob `cf91aede86b094370c42962197a6ac8cb90ecba2`). Quick exits combine
  positive-profit bands with weakening/overextended indicators; long-tagged
  trades take a separate route. The legacy fallback stop is -50%, with other
  conditional exits: that risk geometry is unsuitable to copy into Atlas.
  The current X8 file was listed but its content could not be retrieved in
  this pass; current-version exit claims remain unverified.
- ORB commit `23f2400a696296cddb00166dd429c0d048bee1bb`:
  `src/run_max_backtest.py`
  (blob `a894227da9f2fa0e01980c8db1be1e18145bc2b0`) and
  `src/backtester_v2.py`
  (blob `f988d98ec57b04de5ae6b90a9939742fc9f731ef`). The maximum-history
  runner uses a 20-minute opening range, 0.75-range target, 0.50-range stop,
  1.2 volume threshold and 10:00–11:30 entry window. Its separate default
  signal function has different parameters; inspect the runner used for
  each claim rather than assuming every script represents the same policy.
- Qlib commit `54355232463878d2eebb91fe0ee5fa7fa1f5976c`:
  `examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml`
  (blob `5ae316801531f606a593f5a1bbe418d619533e98`). Configuration trains
  2008–2014, validates 2015–2016, tests 2017–2020, holds 50 stocks and
  replaces up to five per step. The inspected TopkDropout code requests
  previous-step predictions and checks tradability before orders. Cost
  configuration and Chinese market conventions require adaptation.
- Concretum: unversioned webpage, reviewed 2026-10-09. The displayed example
  takes the direction of the first five-minute candle and enters on the
  next bar's open; it does not wait for a later HOD break. Its default has
  no profit target, a range-based stop, and end-of-session exit. Daily ATR
  is shifted; gap stops use the worse opening price; equal-bar target/stop
  hits resolve to stop. No explicit spread or slippage is charged in the
  shown backtest. Do not equate its bar results with Webull fills.

## Reproduced issues in the public ORB backtester

Four tiny synthetic probes ran successfully against the pinned source with
Python, pandas 2.2.3 and numpy 2.3.5. They require no market-data keys.

| Probe | Observed behavior | Implication |
| --- | --- | --- |
| Flat-price round trip, 100 shares, $0.01 slippage and $0.005 commission each side | Slipped fills already produce -$2; reported net is -$5 instead of -$3 after commission. | Slippage is charged twice. This specific error understates returns; it does not make other assumptions conservative. |
| Long stop at $99; next bar opens $95 and trades $94–$96 | Fill recorded at $98.99. | Stop fills ignore worse opening gaps and can overstate results. |
| 0.75-range target versus 1.0-range break-even activation | Position exits at target before break-even activates. | The maximum-history configuration's advertised break-even mechanism cannot operate for normally priced bars. |
| AAA remains open at end of sample; last global bar belongs to BBB | AAA's last price is $100, BBB's is $500; AAA exits at $499.99, recording $7,599.05 synthetic profit. | End-of-sample liquidation can use another instrument's price. |

The last issue only affects positions remaining open at the end of a sample.
Its contribution to the author's historical totals is unknown. The runner's
Sharpe calculation also groups only dates with trades, omitting zero-trade
days; compare calendar-day equity returns before using its risk metrics.
Do not infer the complete strategy is unprofitable from these probes.

## What Atlas already implements

- `quick_scalper.py`: spread plus estimated slippage, liquidity allowance,
  net reward relative to structural risk, and demonstrated bid/ask advance.
  Movement observed already is not a forecast that more movement will occur.
- `forward_runtime.py`: executable-bid peak tracking, staged partial harvest,
  stop replacement, and authoritative position/protection reconciliation.
- `configuration.py`: default first harvest at 0.75R, 25% harvest size,
  moderate peak-retention fraction 35%, stronger tiers at 2R and 4R.
  These are source defaults, not a read of the Windows process's effective
  configuration.
- `momentum_radar.py`: ranked acceleration/source/turnover components and
  the approved 10% promotion rule. `setups.py` already detects micro pullbacks;
  simply adding more indicator names would duplicate existing capabilities.

The earlier NAUT trade's recorded 0.4545R peak never reached the default
0.75R first-harvest threshold. That explains that particular absence of a
harvest; it does not establish an optimal lower threshold. Other trades
already harvested successfully. Separately, some losers showed little
executable profit after entry, so earlier exits alone cannot repair entries.

## Bounded comparison batches to build from

### Batch A: profit capture, fixed entries

Use closed Warrior lifecycles with reliable quote/fill ordering. Compare
current management with first-harvest activation at 0.50R (retain 25% sizing)
and with a separate earlier peak-lock candidate. Change one rule at a time;
keep Scalper separate. These thresholds are declared experimental candidates,
not optimized recommendations. Prefer executable bids and recorded fills;
do not manufacture trades from historical maxima.

Score net expectancy, drawdown, average loser, profit factor, time in trade,
realized share of peak profit, and upside forgone. Include profitable runners
as well as giveback cases. Reusing a small set of observed losers to tune and
declare a winner is not an out-of-sample test.

### Batch B: retained movers, fixed risk and exit policy

Keep the approved 10% promotion rule. Compare current HOD entry selection
against a fresh micro-pullback/reclaim candidate while the mover remains on
the radar. Record age since impulse, extension from the fresh trigger,
pullback depth, bid/ask advance, spread cost, and volume acceleration. Use
volume as ranking/context rather than reintroducing a universal hard RVOL
cutoff. A 10% promotion rule can target the next leg, not catch the first 10%.

Use only information available at decision time, both successful and failed
movers, and historical universe membership. Changing a trigger or stop
after seeing future prices invalidates the comparison.

### Batch C: early-session baseline and ranking

Compare a declared opening-window directional entry and a distinct confirmed
range-break entry. Do not treat these as the same strategy. Separately compare
ranked selection among eligible opportunities with current allocation; Qlib's
top-k structure suggests a method, not transferable model weights or stock
returns. Keep the retained-mover rule as a distinct strategy scope rather
than silently removing its 10% eligibility condition.

## Data and promotion limits

Existing coverage batches selected 40 episodes, but only four closed under all
three replay policies. Those file-order samples cannot rank the strategies.
The existing command compares exit mechanics on fixed planned entries; it
does not yet run the three new batches above or reproduce live Warrior.

The opt-in `EARLY_PARTIAL_V1` extension now provides a preliminary Batch A
mechanics comparison: quarter at 0.75R versus quarter at 0.50R, both with
break-even afterward. This isolates the threshold but does not reproduce
Warrior's current peak-retention management. See
[chronological-exit-comparison.md](chronological-exit-comparison.md) for the
bounded command and separate checkpoint. Batch B and Batch C remain research
specifications, not implemented strategy performance tests.

Advance in small, resumable batches after source coverage is established.
Missing IEX minutes remain unresolved. Check fixed chronological periods,
day/symbol concentration, costs, overlap and shared capital, then reserve a
later period for validation. Repeat tuning on a holdout consumes that holdout.
No paid-data purchase is needed for this code audit; robust performance tests
still require appropriate data. No runtime policy is promoted by this report.
