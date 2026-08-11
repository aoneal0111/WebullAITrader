# Atlas Warrior Momentum V1

`WARRIOR_MOMENTUM_V1` is an isolated Atlas experiment for historical replay,
simulation, and paper trading. It is not an exact reproduction of Ross Cameron's
proprietary or discretionary method, and V1 cannot authorize a live order.
`ATLAS_STRATEGY` defaults to `existing`; selecting `warrior_momentum_v1` must be
explicit. `WARRIOR_MOMENTUM_V1_LIVE_EXECUTION_ENABLED` defaults to `false`, and
the V1 configuration and signal model reject live authorization even if the
environment value is changed.

## Provenance categories

### A. Publicly documented concepts

The broad workflow uses stocks in play, percentage gain, relative volume, float,
catalysts, liquidity, high-of-day momentum, pullbacks, bull flags, flat tops,
breakouts, structural invalidation, and risk-based share sizing. These are general
concepts described publicly in the Warrior/Ross workflow.

### B. Inferences from public workflow descriptions

The ordering `discovery -> stocks in play -> ranking -> setup -> entry -> risk ->
paper execution`, the distinction between a scanner hit and a buy signal, and the
use of several setup states are workflow inferences. They are not claims about a
private scanner or exact discretionary execution.

### C. Atlas engineering approximations

All values below are typed and configurable. Discovery defaults are price
`$1-$20`, change `>=10%`, preferred RVOL `5x`, preferred float `<=20M`, volume
`>=100k`, and dollar volume `>= $1M`. These influence status and ranking; catalyst
absence, temporarily wide spread, high float, or sub-preferred RVOL do not by
themselves remove a symbol from discovery.

The score is bounded to 0-100 and uses Atlas weights—not published Warrior
weights:

| Component | Weight | Normalization |
|---|---:|---|
| Percentage change | 20 | linear from 0 at 0% to 1 at 40% |
| Relative volume | 20 | piecewise: 1x=0, 2x=.35, 5x=.75, 10x+=1 |
| Short acceleration | 15 | linear from .75x to 3x recent mean volume |
| Float | 15 | <=5M=1, <=10M=.85, <=20M=.65, <=50M=.30, >50M=.05; unknown=.25 |
| Dollar volume | 10 | linear from $100k to $10M |
| Catalyst | 10 | TRUE=1, FALSE=.10, UNKNOWN=.30, UNAVAILABLE=.20 |
| Setup quality | 5 | setup score / 100 |
| Spread quality | 5 | `clamp(1 - spread_percent/2, 0, 1)`; unavailable=0 |

Each weighted component is rounded to .01 and their sum is clamped to 0-100.
Discovery statuses default to WATCH at 25, NEAR_QUALIFIED at 45, and QUALIFIED at
60. `SQUEEZE_5_IN_5` is at least 5% close-to-close over timestamp-aligned five
minutes; `SQUEEZE_10_IN_10` is at least 10% over ten minutes. `RUNNING_UP` uses 3%
over five minutes. `HIGH_RELATIVE_VOLUME` starts at 5x. HOD momentum is positive
and within 1% of session high. Top-gapper membership is supplied by rank context,
not inferred from absent cross-sectional data.

Feature construction uses one-minute OHLCV: session and rolling high/low, rolling
change and volume, latest-volume/recent-mean acceleration, typical-price VWAP,
distance to VWAP/HOD, pullback depth, consolidation duration, prior resistance,
and latest-volume/recent-mean breakout ratio. Missing source data stays missing.

Setups are mechanical:

- `HIGH_OF_DAY_BREAKOUT`: at least five bars, price near prior session high,
  three-bar range no wider than 3%, close above prior resistance plus .05%, and
  latest volume at least 1.2 times the recent mean. Stop: recent swing low.
- `MICRO_PULLBACK`: three-bar impulse of at least 4%, two-bar pullback no deeper
  than 3%, higher/equal second low and reduced selling volume, then close through
  pullback resistance plus .05%. Stop: pullback low.
- `BULL_FLAG`: four-bar pole of at least 4%, three-bar controlled flag retracing
  10-50% of the pole with a higher/equal final low, then close through flag
  resistance plus .05%. Stop: flag low.
- `FLAT_TOP_BREAKOUT`: at least three tests within .35% of common resistance,
  non-declining support, then close through resistance plus .05%. Stop: support;
  the model records `BREAKOUT_LEVEL` as its structural model.

Insufficient bars yield `UNKNOWN`; failed geometry yields `NOT_FORMED`; valid
geometry below the trigger yields `FORMING`; only a confirmed break is
`TRIGGERED`.

### D. Atlas safeguards

Entry defaults require score >=60, triggered supported setup with setup score
>=60, a positive structural stop no more than $1 per share away, spread <=1%,
dollar volume >=$5M, TRUE earnings/SEC catalyst, tradability, non-halted status,
and PREMARKET or REGULAR session. The conservative catalyst requirement is only
for this experimental entry stage. The production catalyst rule is unchanged.

Sizing is `risk_dollars = min($100, .005 * equity)` and
`shares = floor(risk_dollars / risk_per_share)`, then capped by buying power,
10,000 shares, $25,000 position value, allowed symbols, exposure, existing Atlas
risk approval, and broker/account restrictions. These adapters do not replace
the core risk engine or order safeguards.

Paper management plans 50% at 1R, 25% at 2R, and a 25% runner; integer remainder
goes to the runner. At 1R the simulated stop may move to breakeven. A supplied
structural trailing level can only tighten the stop. Session and setup are retained
on signals/results. Halt tracking records entry, duration, resume time, and gap;
it never generates halt-resumption entries.

Replay reports discovery/setup/signal counts, win/loss rates, profit factor,
expectancy, average/median R, drawdown, hold time, best/worst trade, MAE/MFE,
slippage and spread sensitivities, plus breakdowns by setup, float, price, RVOL,
catalyst, session, and momentum score. Same-dataset A/B rows compare current Atlas
and V1 candidate/trade frequency, win rate, profit factor, expectancy, drawdown,
average risk, and turnover. No superiority conclusion is produced automatically.

Atlas Focus projection includes interesting pre-entry candidates and exposes rank,
symbol, last, change, RVOL, float, volume, dollar volume, spread, catalyst, score,
setup/state, HOD distance, session, and status. Row selection continues through the
existing operator inspection store, so a chart remains pinned if ranking membership
later changes. Explanations are deterministic facts and reason codes; no LLM is in
the execution path. Near-miss telemetry aggregates reason counts and keeps only a
bounded recent-symbol window rather than publishing every market event.

## Forward paper capture

The forward sidecar wraps the unchanged `WarriorMomentumRuntime`; it does not
replace Current Atlas, own a broker port, or expose any live-order method. The
bounded Webull validation command constructs only an official market-data client:

```powershell
python -m app.strategies.warrior_momentum.capture_forward_live --limit 10
```

The default append-only store is
`data/warrior_momentum_v1_forward/forward_capture.sqlite3`. SQLite WAL mode,
`synchronous=FULL`, a schema-version metadata row, an autoincrement sequence,
and a unique deterministic SHA-256 record ID provide restart safety, ordering,
crash recovery, and duplicate suppression. Payload JSON is canonical and rejects
credential-, token-, session-, and account-identifier field names. Schema V1
record types are `DISCOVERY`, `STATE_TRANSITION`, `MINUTE_BAR`, `DECISION`,
`CATALYST_EVIDENCE`, `SPREAD_EVIDENCE`, `DATA_QUALITY`, `PAPER_FILL`,
`COUNTERFACTUAL`, and `DAILY_REPORT`.

Each discovery record contains point-in-time price/change, bid/ask/spread,
volume/RVOL/average volume/dollar volume, float plus provenance, earnings/SEC
catalyst state and source, tradability, halt state/certainty, score, session,
and stocks-in-play reasons. A decision record contains only completed bars
available by its timestamp, the exact bar IDs, derived features, score components,
setup geometry, status, and reason codes. Separate spread and catalyst evidence
records retain their observation timestamps; missing evidence remains `null`,
`UNKNOWN`, or `UNAVAILABLE`. Float provenance is one of authoritative float,
shares outstanding, market-cap/price proxy, or unknown.

The state machine records `DISCOVERED`, `WATCH`, `NEAR`, `QUALIFIED`,
`SETUP_FORMING`, `SETUP_TRIGGERED`, `ENTRY_READY`, `ENTRY_BLOCKED`,
`PAPER_ENTRY`, `PAPER_PARTIAL`, and `PAPER_EXIT`. Every blocked triggered setup
stores all failed deterministic gates and observed limits. Triggered,
score-qualified candidates blocked from entry enter a separate 60-completed-bar
counterfactual stream. They never create a paper fill and are excluded from V1
performance.

Approved paper signals use the existing risk-based sizing and V1 management:
50% at 1R, 25% at 2R, runner remainder at 3R, breakeven after 1R, and a structural
low trail. Same-bar ambiguity is stop-first. Positions and counterfactual paths
continue through direct completed-bar observations even after scanner removal,
and active paper positions recover from immutable fills after restart. All paper
fills explicitly store `live_execution_authorized=false`.

Capture uses a background bounded queue (4,096 records, 128-record batches,
250 ms maximum idle flush by default), outside Qt. Queue saturation applies a
synchronous durable fallback instead of discarding a critical record. Metrics cover
queue depth, written and duplicate records, write latency, dropped submissions,
and GUI refresh frequency. The capture package imports no Qt module and emits no
GUI refresh itself.

`replay_captured_decision` reconstructs a scanner observation and exact completed
bar set entirely from the durable records, runs the frozen V1 runtime, and compares
score, status, setup, and reason codes. `build_daily_report` produces the discovery
funnel, completed-paper-trade R metrics, rejection-gate counts, and missing-data
counts; performance fields are `N/A` (`None`) when no completed sample exists.

## Mission Control forward-paper mode

Forward collection is explicitly opt-in and remains unrelated to execution
authorization:

```text
WARRIOR_FORWARD_PAPER_ENABLED=false
WARRIOR_FORWARD_CAPTURE_PATH=data/warrior_momentum_v1_forward/forward_capture.sqlite3
```

The desktop runtime driver owns the enabled sidecar lifecycle. Start opens the
append-only database, starts the bounded writer, restores completed bars and open
paper/counterfactual state, and writes an observation-session START record. The
existing scanner normalizes each Webull event before delivering that same event
to Warrior; no second streaming transport is created. Stop ends market-data
delivery, generates the daily report, writes the session END record, flushes, and
closes the writer before broker disconnection. Application shutdown repeats this
idempotently.

Trades are aggregated into timestamp-aligned one-minute OHLCV bars. A symbol is
evaluated on its first complete normalized observation and when a minute completes;
same-minute ticks do not create strategy decisions or Qt updates. Open paper
symbols remain in the retained subscription set across reconnect/universe refresh,
and immutable fills restore paper state after restart.

Each observation run stores strategy version, schema version, New York trading
date, start/end timestamps, environment, and a SHA-256 configuration fingerprint.
The fingerprint covers every `WarriorMomentumConfig` field and the strategy
version using canonical sorted JSON. It contains no credential, account, or Webull
session identifiers. Cumulative reports are separated by fingerprint and never
merge incompatible configurations.

Atlas Focus offers `CURRENT ATLAS` and `WARRIOR PAPER`. Current Atlas keeps its
existing projection. Warrior Paper polls a coalesced immutable snapshot once per
second and shows rank, score, status, change, RVOL, float/provenance, spread,
catalyst, setup/state, trigger, stop, and blocking gates. Row clicks retain the
existing operator-inspection behavior. The compact summary and funnel show
discovery through paper trade, use `N/A` for unmeasured R, and keep counterfactual
counts separate from paper performance.

Warrior health is independently `DISABLED`, `STARTING`, `RUNNING`, `DEGRADED`, or
`STOPPED`. Backlog or writer failure does not change broker/stream health. Metrics
cover queue depth, persisted/duplicate/dropped records, batch latency, synchronous
fallbacks, paper publication rate, and GUI refresh rate.

Daily reporting includes funnel/setup/entry counts, paper and open trades, R
statistics, blocked gates, counterfactuals, and data quality. Compatible cumulative
reporting is available with:

```powershell
python -m app.strategies.warrior_momentum.report_forward_capture
```

It breaks completed trades down by setup, score, RVOL, float provenance/bucket,
price, catalyst, and session. Evidence maturity is descriptive only: `NO_TRADES`
at 0, `EARLY_SAMPLE` at 1-19, `DEVELOPING_SAMPLE` at 20-99, and
`MEANINGFUL_SAMPLE` at 100 or more completed paper trades.
