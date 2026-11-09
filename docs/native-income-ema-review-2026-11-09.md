# Native EMA review — November 9, 2026

Native verification is complete for the recent four-hour EMA50/EMA100 candidate.
The conservative native version reproduces all sixteen screening entry and exit
timestamps, with fifteen profitable closes and one loss. No worker was installed,
started or changed, and no live/paper orders or Grok calls were made.

## Native results

Kraken USD candles, August 15–November 8, 2026 (85 completed calendar days),
six shared position slots, no borrowing, current recorded Kraken precision and
market catalog, Freqtrade 2026.9 / CCXT 4.5.84. Results below use the causal
allocation variant, no added entry delay, and illustrative $2/calendar-day overhead.

| Initial USD | Fee each side | Closes / wins | Average net/day | Bar-close marked drawdown | Worst net marked day |
|---|---:|---:|---:|---:|---:|
| 10,000 | 0.4% | 16 / 15 | 25.81 | 5.91% | -551.34 |
| 10,000 | 0.8% | 16 / 15 | 23.18 | 5.94% | -544.39 |
| 100,000 | 0.4% | 16 / 15 | 202.83 | 3.19% | -2,509.28 |
| 100,000 | 0.8% | 16 / 15 | 185.23 | 3.65% | -2,486.12 |

Native market-order backtests have **no explicit slippage model**. The comparable
screening model with zero slippage averaged 26.09/day on 10k at 0.4% fees;
native averaged 25.81/day. Differences in native wallet sizing and venue precision
remain. The earlier screen's 25.83/day figure includes 0.05% slippage per side.
The similar rounded averages do not imply exact dollar reconciliation between
the two models. Native-fill cash and PnL do independently reconcile.

The 100k scenario is not a recommendation to fund a larger account. These are
the same short period and selected strategy already examined in the screen,
not an independent forward test. Earlier historical checks of this EMA case
lost money. Nothing here establishes dependable $100/day income or a future
93.8% win rate.

## Why preliminary native results showed 100% wins

Two differences were isolated and retained in the evidence.

1. **Indicator initialization.** The first native run loaded 200 pre-period
   bars; the sealed snapshot contained 205 pre-period bars for the ready markets.
   That small difference moved ETH's first crossover by one candle. The aligned
   native variant loads all 205 actual pre-period bars, while retaining the
   200-consecutive-bar signal eligibility rule. No missing prices were created.
2. **Same-candle portfolio allocation.** Native pair-by-pair backtesting allowed
   an intrabar BTC ROI close to release a slot/funds for a VET entry at that
   candle's opening price. The BTC candle opened at 73,001.2; the modeled ROI
   exit was 74,284.5, reached later within the candle whose high was 75,744.2.
   The conservative screening engine makes all opening allocation decisions
   before processing intrabar exits. It skipped VET and later took XLM, which
   lost. The unguarded native model took VET and excluded XLM, yielding sixteen
   wins instead of fifteen.

The research-only native subclass now reserves slots and proceeds released by
ROI/stop exits processed at the same candle timestamp. It permits genuine prior
candle releases and distinguishes planned opening exits. This is a conservative
backtest timing policy; it is not a claimed repair to Atlas's running broker or
proof of tick-level order sequencing. It does not synthesize missing detail bars.

After alignment and the guard, the baseline native and screening models have
the same sixteen filled symbols, entry timestamps and exit timestamps, including
the XLM loss. One same-candle slot rejection was recorded in the 10k baseline.

## Risk visibility

Native closed-trade drawdown was misleadingly small when the preliminary ledger
contained only winning closes. A separate reconstruction from native fills and
sealed bar closes marks open inventory with modeled exit fees. The conservative
10k / 0.4% scenario has approximately 5.9% marked drawdown and a -551 worst
marked day, with 68 negative net marked days after overhead. This is bar-close
risk, not a verified intrabar path or a maximum-loss bound.

## Scope and verification

40 native strategy runs completed across three diagnostic phases: 8 preliminary,
16 aligned and 16 allocation-guard comparisons. Alias/control runs are repeats,
not independent strategy discoveries. Multi-strategy native batches share their
largest startup count; a base-200 alias in an aligned batch is not a separate
200-bar control. The original separate preliminary batch is preserved.

The final batch compared standard, four-hour delayed-entry and conservative
allocation variants at 10k/100k capital and 0.4%/0.8% fees. The delayed-entry case
is a four-hour stress scenario, not a measurement of Grok latency or a selected
improvement. All native strategy-wrapper callback audits recorded zero failures.

53 focused tests passed, including ATR anchoring, fee-aware physical ROI targets,
native/screen signal formulas, timeout timing, same-bar slot/cash reservation,
open-loss visibility, independent wallet parity and the prior research checks.
The event-traced screening function is compiled only from this repository's own
engine AST and checked against the unchanged accelerated outputs. No provider
text or downloaded external program is evaluated as executable code.

One new public AssetPairs catalog request was made; the other phases reuse it.
All backtest network sockets were disabled. The native tests use thirteen ready
markets, matching the current four-hour screen. ONE was excluded because its
captured history did not satisfy the screen's history-readiness requirement.

Limits remain: current catalog rules do not verify August execution rules;
OHLC fills omit queue position, explicit slippage and intrabar sequencing;
fees/overhead are assumptions; daily cash withdrawals are not simulated. This
prototype and its portfolio policy have not been qualified for unattended
deployment or substituted for existing Atlas strategy controls.

## Reproduction

Use an isolated Python 3.13 environment with
`requirements-native-income-research.txt`. The native review driver accepts
sealed captures from the prior income archive:

```
python -m app.strategy_lab.native_income_ema_review --root NEW_REVIEW_DIR --capture ARCHIVE/current-kraken --grid ARCHIVE/current-grid --prepare
```

`--reuse-catalog PATH` reuses a retained catalog and makes no new catalog request.
`--compare-only` derives trade comparisons from existing results; `--marks-only`
derives open-inventory marks without rerunning backtests. Existing native result
directories are never overwritten. Original first-phase strategy bytes are
retained in `docs/research-snapshots/native-income/initial-ema.py.txt`.

Decision: native candidate verified within this modeled window; retain for
research, with existing production settings unchanged.
