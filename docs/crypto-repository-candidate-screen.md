# Frozen crypto repository candidate comparison

The capped NFI study produced no basis for admission. This next screen reuses
the existing official Kraken archive inputs without downloading more market
data and without changing any Atlas or NFI setting.

## Reviewed sources

Repository: https://github.com/freqtrade/freqtrade-strategies
Pinned commit: `f3340ce11f5bdf62f598522e64d1f5638eaa13f5`.
Each source file and the GPL-3.0 license is hashed in
`app/strategy_lab/candidate-source-lock.json`. The repository describes its
strategies as educational starting points, not verified profitable bots.
No author win rate is assigned to these adaptations.

| Candidate | Source timeframe | Entry family | Source stop |
|---|---|---|---:|
| Strategy001 | 5m | EMA20/50 upward cross with a bullish Heikin-Ashi bar | -10% |
| BbandRsi | 1h | RSI below 30 and close below the lower Bollinger band | -25% |
| ADXMomentum | 1h | ADX/momentum/positive directional strength | -25% |

These three were selected for different entry families before their new test
results were read. No source is vendored into Atlas: the launcher checks out
and verifies the pinned public files in a separate local research directory.
They use existing Freqtrade/TALib/qtpylib libraries. No new framework or trading
account is installed. Deprecation notices from the old qtpylib import path do
not change the pinned code; it remains supported in the pinned runtime.

## Frozen method

The `frozen-protocol.json` file is written before backtesting. It freezes:

- Eight USD pairs from the completed Kraken history manifest.
- July 1–August 31, 2026 for development; September 1–30 for temporal validation.
- Original source signals and source exit rules, plus a distinct variant
  retaining those rules but replacing the stop with -2%.
- 400 startup candles for all candidates, 1m detail simulation, $1,000 virtual
  wallet, $50 stake, two open positions, no shorting or position additions.
- Costs of 0.4% and 0.6% per side as explicit assumptions, not verified fees.
- Fresh virtual state for each period/scenario. No positions carry between
  development and validation; forced period-end exits remain visible.

The source-exit variants are comparison-only and cannot be selected for paper
admission, regardless of their apparent win rate or profit.

Development selection requires the capped variant to pass both cost scenarios:
at least 50 non-forced closed trades, positive net PnL, win rate >=55%, profit
factor >=1.2, and closed-trade account drawdown <=5%. Sampled prefix-invariance
checks must also pass. Select the candidate with the highest development net
PnL at the higher cost, breaking ties by class name. This selection is written
to `development-selection.json` before any candidate validation backtest.

Only that selected capped candidate receives validation at both costs. The
validation gate has the same net/win-rate/profit-factor/drawdown requirements
and at least 30 non-forced closed trades. A failure cannot cause switching to
another candidate based on validation results. If development has no qualifier,
validation is not opened. No parameter search, hyperopt or result-dependent
threshold adjustment occurs.

The minimum sample counts exclude `force_exit`; the displayed total win rate,
net PnL and profit factor include native period-end closes. Reports include
exit reasons and monthly breakdowns to make that distinction inspectable.
A passed gate creates only a research shortlist. It never changes a strategy
release registry, entry lease, broker setting or desktop decision path.

## Technical checks

The native Freqtrade backtester receives only hash-verified archived candles
and frozen public market metadata. The container has `--network none`;
Python socket operations are also blocked. The original lab and desktop are
not mounted. Source and history mounts are read-only. Only the isolated
result/scenario directories are writable. No model client or credentials are
loaded. The generated subclasses refuse both LIVE and DRY_RUN modes and
require a marker in their separate BACKTEST directory.

For each candidate, sampled prefix-invariance probes use BTC/USD and XRP/USD
at July 15, August 15 and September 15 cutoffs. Indicators and entry/exit
columns computed with 100 later rows are compared with results computed only
through the cutoff. The last 200 preceding rows must match within stated
floating-point tolerance. This is a sampled causality check on raw archive
frames, not comprehensive certification. A synthetic negative-shift fixture
verifies that the audit detects an intentional future-data read.

The focused tests also check immutable source hashes, rejection of LIVE/DRY_RUN,
frozen date/cost/risk settings, exclusion of source-stop variants from
selection, cost-stress failures, sample limits, and the inability of a high
win rate to override risk/net-profit requirements.

## Interpretation limits

This is retrospective temporal validation of candidates selected after the
quarter. The period's regime and NFI results were already observed, and source
authors may have tuned on overlapping or later data. September is not a new
prospective holdout. The prefix probes also read September inputs and signal
columns for technical auditing before selection, without calculating that
period's candidate performance. "Validation not opened" means the candidate
validation backtest was not run, not that no September inputs were read. Present-day pair availability creates survivorship and
universe-selection bias. Current public market metadata approximates historical
precision and minimums. The quarter cannot prove robustness across regimes.

The native simulator uses candle price assumptions and may fill archived
no-trade gaps with flat zero-volume candles. It does not reconstruct actual
spread, liquidity, queue position or account entitlements. Sampled prefix
checks do not replace complete lookahead/recursive-stability analysis. The
screen does not reproduce Grok decisions, controller leases, bootstrap duration
or campaign loss-stop behavior. Fresh-quote validation and a prospective
isolated paper trial would remain separate requirements even for a shortlisted
candidate. Live trading and Grok order submission remain disabled.

## Operator run

`tools/run-crypto-candidate-screen.ps1` requires the exact reviewed revision
and an explicit existing `frozen-history` directory. It downloads only the
pinned strategy source checkout, verifies the existing data, and invokes the
pinned Freqtrade Docker image offline. It does not download market history or
start a trading loop. `candidate-screen-summary.json` is the small private
upload artifact. Raw data and detailed trade records stay local; performance
is not published in the repository.

## Implementation validation

The focused suite passes 86 disconnected tests. The actual pinned source
files were also executed through native Freqtrade 2026.9 against the frozen
archive: 12 development scenarios completed, and all 18 sampled prefix probes
passed. This package environment is not the pinned Docker image; Windows
container replication remains separate. No candidate met the preregistered
development gate, so no September performance backtest was run and no
candidate was promoted. Detailed simulated performance remains in the private
summary rather than repository documentation.
