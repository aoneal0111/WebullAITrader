# WARRIOR_MOMENTUM_V1 Replay/Paper Baseline

This report characterizes the unchanged V1 configuration. It does not optimize
thresholds and does not authorize or route any order. Machine-readable results
are in `data/warrior_momentum_v1_validation/results/report.json`; the deterministic
ledger is `trade_ledger.csv`.

## Dataset and bias boundary

Dataset `warrior-v1-2c0ddf816eb82a40` contains 19,800 one-minute Webull OpenAPI
OHLCV bars for AUUD, BWMN, DKI, HZO, JWEL, OFAL, SCKT, STFS, STKH, THH, VREX,
and WAFU. The immutable bars SHA-256 is
`2c0ddf816eb82a40c14aebbd836e7bc403fe6aa1fbc324614fe88ee87801a41d`.
Raw sparse history spans 2025-11-04 through 2026-08-10. The exact evaluable
range with prior-close and prior-volume reference data is 2026-06-12 through
2026-08-10: 273 symbol-sessions. Each symbol has 1,650 bars; per-symbol date and
session coverage is recorded in `manifest.json`.

Symbol selection came from the 2026-08-10 production scanner observation, so
the dataset has material selection/survivorship bias. It is suitable for an
initial pipeline and eligibility audit, not a claim of population-level
performance.

Historical catalyst evidence is UNAVAILABLE because Atlas has no point-in-time
earnings/SEC archive. Historical spread is UNAVAILABLE because OHLCV has no
bid/ask. Float is UNKNOWN because applying the current market-cap/price proxy to
past bars would introduce lookahead. Halt and historical tradability evidence
are also unavailable. Only REGULAR-session bars were returned reliably.

## Lookahead audit

Bar timestamps are interval opens. A bar at 09:30 becomes eligible at 09:31.
The runtime now filters every feature tuple through this completion boundary.
Session high, rolling high/low, VWAP, volume acceleration, setup geometry, and
breakout confirmation therefore contain completed bars only. Previous close
and average volume are calculated from prior completed daily bars. Top-gapper
membership uses only the just-completed cross-section. Replay fills use bars
opening at or after signal time; configured delay skips eligible future bars.
Stops and targets never inspect a later candle early, and same-bar ambiguity is
stop-first. Tests prove future-bar invariance and these boundaries.

## Exact-strategy result

| Measure | Result |
|---|---:|
| Discovered symbol-sessions | 273 |
| Stocks in play | 214 |
| Near-qualified | 17 |
| Qualified by score | 11 |
| Setups detected | 409 |
| Setups triggered | 208 |
| ENTRY_READY signals | 0 |
| Paper trades | 0 |

Because V1 conservatively requires a TRUE catalyst and an observed spread at
entry, the dataset cannot produce an evidence-complete signal. Total R,
expectancy, profit factor, win rate, drawdown, MAE/MFE, hold time, streaks, daily
risk, and break-even slippage are **not measurable**, rather than zero-return
performance. IDEALIZED, BASELINE, and CONSERVATIVE fill engines were exercised
in deterministic tests but were not applied to non-signals.

## Setup characterization

| Setup | Detected | Triggered | Trades | Expectancy |
|---|---:|---:|---:|---|
| High-of-day breakout | 132 | 48 | 0 | Not measurable |
| Micro pullback | 38 | 10 | 0 | Not measurable |
| Bull flag | 64 | 25 | 0 | Not measurable |
| Flat-top breakout | 175 | 125 | 0 | Not measurable |

Flat tops account for 60.1% of unique triggered setup/session combinations.
That is detector frequency, not evidence that flat tops are profitable.

## Candidate breakdowns

Peak score per evaluable symbol-session is used so long sessions do not receive
hundreds of candidate votes.

| Score | Candidates | Trades |
|---|---:|---:|
| <25 | 163 | 0 |
| 25-44 | 93 | 0 |
| 45-59 | 6 | 0 |
| 60-69 | 3 | 0 |
| 70-79 | 8 | 0 |
| 80-89 | 0 | 0 |
| 90-100 | 0 | 0 |

| RVOL | Candidates | Trades |
|---|---:|---:|
| <2x | 237 | 0 |
| 2-5x | 12 | 0 |
| 5-10x | 8 | 0 |
| 10-25x | 8 | 0 |
| 25x+ | 8 | 0 |

| Price | Candidates | Trades |
|---|---:|---:|
| <$1 | 85 | 0 |
| $1-$2 | 73 | 0 |
| $2-$5 | 71 | 0 |
| $5-$10 | 12 | 0 |
| $10-$20 | 9 | 0 |
| >$20 | 23 | 0 |

All 273 float values are UNKNOWN, all catalyst states are UNAVAILABLE, and all
273 evaluable peak observations are REGULAR session. No claim about float,
catalyst, or session expectancy is supported.

## Stops and execution

Triggered-bar structural risk distances averaged $0.0632 for BREAKOUT_LEVEL,
$0.0343 for FLAG_LOW, $0.1259 for MICRO_PULLBACK_LOW, and $0.0949 for
RECENT_SWING_LOW. No observed structural risk exceeded the configured $1 maximum.
Stop-out frequency and winner MAE cannot be measured without eligible trades.

The paper engine implements 50% at 1R, 25% at 2R, 25% runner, breakeven after
1R, prior-completed-bar structural trailing, partial fills, entry delay, spread,
and slippage. Same-bar stop/target ambiguity is stop-first. Execution sensitivity
and break-even slippage remain unavailable for this dataset because applying
fill assumptions to candidates rejected for missing entry evidence would no
longer validate the exact strategy.

## CURRENT_ATLAS comparison

Across the same 273 evaluable symbol-sessions, CURRENT_ATLAS produced zero fully
qualified candidates because its strict catalyst/spread gates faced the same
evidence gaps. WARRIOR_MOMENTUM_V1 retained 11 score-qualified discovery
candidates but produced no trades. Outcome metrics are therefore not comparable;
no winner is declared.

## Failure modes and conclusion

At peak session observations, missing spread blocked all 273 and unavailable
catalyst evidence blocked all 273. Other recurring characteristics were score/risk
gate failure (262), change below 10% (252), low liquidity (252), RVOL below 5x
(249), no triggered setup (219), price below $1 (85), and price above $20 (23).
The detector also frequently selected flat tops, which should be watched for
false-break frequency when evidence-complete paper captures become available.

The evidence does **not** establish positive or negative expectancy. Continued
paper development is supported only to collect point-in-time scanner snapshots,
bid/ask, earnings/SEC evidence timestamps, float provenance, halt state, and the
resulting ENTRY_READY signals. Parameter changes are not justified by this run.
