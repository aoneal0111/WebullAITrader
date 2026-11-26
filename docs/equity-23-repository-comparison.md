# Public repository comparison for the historical 23 strategies

Reviewed 2026-10-28. This catalogue adds six pinned external references alongside all 23 existing historical families. It does not install a bot, reproduce author performance, add dependencies, alter execution algorithms or qualify a strategy. Upstream files were cloned for source inspection only; no upstream Python was run.

| Source | Evidence and use |
|---|---|
| [sam-bateman/trading-orb](https://github.com/sam-bateman/trading-orb/tree/23f2400a696296cddb00166dd429c0d048bee1bb) | README reports 4292 trades, 50.3% win rate and PF 1.31 on a selected 20-stock universe; not reproduced here. |
| [solste7en/strategy-research](https://github.com/solste7en/strategy-research/tree/7dae4258cf1c7859ce11d0ae57b36e2bb70557b5) | Source provides walk-forward harness and synthetic tests; result CSVs are git-ignored, so no independently reproduced profitability. |
| [wwakeford/breakout-retest-backtest](https://github.com/wwakeford/breakout-retest-backtest/tree/7cb09a4e188b895af3ad088ca1557edb3c7c96f0) | README includes an example 64.29% win-rate output, not a supplied reproducible trade ledger. |
| [naibai101/breakout-backtester](https://github.com/naibai101/breakout-backtester/tree/9a705001205dd95d8f1fc39389e262d60d0344a6) | Default demo generates five synthetic momentum tickers; demo results are not market-data performance. |
| [pderer/orb_strategy](https://github.com/pderer/orb_strategy/tree/805a0a68e7f9d84991b99d414348c9cae65d2729) | Inspectable ORB/RVOL calculation; positive daily return ratio is not a net per-trade win rate. |
| [Ascensao/intraday-momentum-strategy](https://github.com/Ascensao/intraday-momentum-strategy/tree/5a9e956994d1ca9ef16e5e0ae6af4ce4e04e87eb) | SPY noise-band replication with return charts; not independently reproduced Atlas profitability. |

## Source findings

### sam-bateman/trading-orb

Revision: `23f2400a696296cddb00166dd429c0d048bee1bb`. License review: MIT.

- Published ten-year configuration uses a 20-minute range; orb_strategy.py contains an earlier 30-minute variant.
- backtester_v2._close_position subtracts a slippage-cost term after using adversely slipped fill prices; reproduction must reconcile this accounting.
- add_relative_volume includes current-bar volume in its rolling mean; use a separately specified prior-only baseline for an Atlas adaptation.

Inspected files (SHA-256 values are in `app/strategy_research/equity_repository_sources.json`):

- [README.md](https://github.com/sam-bateman/trading-orb/blob/23f2400a696296cddb00166dd429c0d048bee1bb/README.md)
- [LICENSE](https://github.com/sam-bateman/trading-orb/blob/23f2400a696296cddb00166dd429c0d048bee1bb/LICENSE)
- [src/run_max_backtest.py](https://github.com/sam-bateman/trading-orb/blob/23f2400a696296cddb00166dd429c0d048bee1bb/src/run_max_backtest.py)
- [src/run_12m_optimization.py](https://github.com/sam-bateman/trading-orb/blob/23f2400a696296cddb00166dd429c0d048bee1bb/src/run_12m_optimization.py)
- [src/backtester_v2.py](https://github.com/sam-bateman/trading-orb/blob/23f2400a696296cddb00166dd429c0d048bee1bb/src/backtester_v2.py)
- [src/intraday_data.py](https://github.com/sam-bateman/trading-orb/blob/23f2400a696296cddb00166dd429c0d048bee1bb/src/intraday_data.py)

### solste7en/strategy-research

Revision: `7dae4258cf1c7859ce11d0ae57b36e2bb70557b5`. License review: NO_EXPLICIT_LICENSE_FOUND.

- VWAP reversion and late-day morning-return continuation are different strategies from early small-cap pullbacks.
- Do not infer SIP entitlements or fees from README; no provider calls or subscriptions are required for this review.

Inspected files (SHA-256 values are in `app/strategy_research/equity_repository_sources.json`):

- [README.md](https://github.com/solste7en/strategy-research/blob/7dae4258cf1c7859ce11d0ae57b36e2bb70557b5/README.md)
- [strategy/strategies/opening_range_breakout.py](https://github.com/solste7en/strategy-research/blob/7dae4258cf1c7859ce11d0ae57b36e2bb70557b5/strategy/strategies/opening_range_breakout.py)
- [strategy/strategies/vwap_reversion.py](https://github.com/solste7en/strategy-research/blob/7dae4258cf1c7859ce11d0ae57b36e2bb70557b5/strategy/strategies/vwap_reversion.py)
- [strategy/strategies/intraday_momentum_continuation.py](https://github.com/solste7en/strategy-research/blob/7dae4258cf1c7859ce11d0ae57b36e2bb70557b5/strategy/strategies/intraday_momentum_continuation.py)
- [strategy/backtest.py](https://github.com/solste7en/strategy-research/blob/7dae4258cf1c7859ce11d0ae57b36e2bb70557b5/strategy/backtest.py)
- [tests/test_backtest.py](https://github.com/solste7en/strategy-research/blob/7dae4258cf1c7859ce11d0ae57b36e2bb70557b5/tests/test_backtest.py)

### wwakeford/breakout-retest-backtest

Revision: `7cb09a4e188b895af3ad088ca1557edb3c7c96f0`. License review: EDUCATIONAL_TEXT_NOT_EXPLICIT_LICENSE.

- Source combines resistance, higher lows, volume breakout, retracement and SMA200.
- No source code copied or imported; README License section is educational text without a standard license grant.

Inspected files (SHA-256 values are in `app/strategy_research/equity_repository_sources.json`):

- [README.md](https://github.com/wwakeford/breakout-retest-backtest/blob/7cb09a4e188b895af3ad088ca1557edb3c7c96f0/README.md)
- [strategy.py](https://github.com/wwakeford/breakout-retest-backtest/blob/7cb09a4e188b895af3ad088ca1557edb3c7c96f0/strategy.py)
- [indicators.py](https://github.com/wwakeford/breakout-retest-backtest/blob/7cb09a4e188b895af3ad088ca1557edb3c7c96f0/indicators.py)
- [utils.py](https://github.com/wwakeford/breakout-retest-backtest/blob/7cb09a4e188b895af3ad088ca1557edb3c7c96f0/utils.py)
- [config.py](https://github.com/wwakeford/breakout-retest-backtest/blob/7cb09a4e188b895af3ad088ca1557edb3c7c96f0/config.py)

### naibai101/breakout-backtester

Revision: `9a705001205dd95d8f1fc39389e262d60d0344a6`. License review: NO_EXPLICIT_LICENSE_FOUND.

- download_data assigns a completed weekly BullFlag to daily dates within that week; needs completed-prior-week alignment before causal replay.
- Daily/weekly setup is not a minute scalper; current source pivot and parameters differ from README description.

Inspected files (SHA-256 values are in `app/strategy_research/equity_repository_sources.json`):

- [README.md](https://github.com/naibai101/breakout-backtester/blob/9a705001205dd95d8f1fc39389e262d60d0344a6/README.md)
- [strategy.py](https://github.com/naibai101/breakout-backtester/blob/9a705001205dd95d8f1fc39389e262d60d0344a6/strategy.py)
- [demo.py](https://github.com/naibai101/breakout-backtester/blob/9a705001205dd95d8f1fc39389e262d60d0344a6/demo.py)

### pderer/orb_strategy

Revision: `805a0a68e7f9d84991b99d414348c9cae65d2729`. License review: NO_EXPLICIT_LICENSE_FOUND.

- daily_return checks stop only from the candle after entry, omitting entry-candle stop ambiguity.
- Daily ATR query ends at the trading-date end; current-day availability must be excluded explicitly.
- No spread, latency or account-cost reproduction; no source imported.

Inspected files (SHA-256 values are in `app/strategy_research/equity_repository_sources.json`):

- [README.md](https://github.com/pderer/orb_strategy/blob/805a0a68e7f9d84991b99d414348c9cae65d2729/README.md)
- [returncalc/daily_return.py](https://github.com/pderer/orb_strategy/blob/805a0a68e7f9d84991b99d414348c9cae65d2729/returncalc/daily_return.py)
- [filters/relative_volume_score.py](https://github.com/pderer/orb_strategy/blob/805a0a68e7f9d84991b99d414348c9cae65d2729/filters/relative_volume_score.py)
- [tests/returncalc_test.py](https://github.com/pderer/orb_strategy/blob/805a0a68e7f9d84991b99d414348c9cae65d2729/tests/returncalc_test.py)

### Ascensao/intraday-momentum-strategy

Revision: `5a9e956994d1ca9ef16e5e0ae6af4ce4e04e87eb`. License review: NO_EXPLICIT_LICENSE_FOUND.

- Backtest uses volatility targeting up to 4x leverage and commission minimums; Atlas long-only variants are materially different.
- SPY band strategy is outside the exact 23 strategy taxonomy; kept as a separate benchmark rather than relabeled.

Inspected files (SHA-256 values are in `app/strategy_research/equity_repository_sources.json`):

- [README.md](https://github.com/Ascensao/intraday-momentum-strategy/blob/5a9e956994d1ca9ef16e5e0ae6af4ce4e04e87eb/README.md)
- [prepare_indicators.py](https://github.com/Ascensao/intraday-momentum-strategy/blob/5a9e956994d1ca9ef16e5e0ae6af4ce4e04e87eb/prepare_indicators.py)
- [backtest_strategy.py](https://github.com/Ascensao/intraday-momentum-strategy/blob/5a9e956994d1ca9ef16e5e0ae6af4ce4e04e87eb/backtest_strategy.py)
- [check_results.py](https://github.com/Ascensao/intraday-momentum-strategy/blob/5a9e956994d1ca9ef16e5e0ae6af4ce4e04e87eb/check_results.py)

## Mapping to the 23 existing families

These are research relationships, not one-for-one implementations. Where no exact repository match was verified, the catalogue says so. It does not invent an external equivalent for premarket or gap strategies. The SPY noise-band repository remains a separate benchmark outside the exact 23.

| Atlas family | Related source IDs | Relationship |
|---|---|
| ASCENDING_BASE_BREAKOUT | CONSOLIDATION_WEEKLY, BREAKOUT_RETEST | RELATED_STRUCTURE_DIFFERENT_TIMEFRAME_AND_RULES |
| BREAKOUT_RETEST_CONTINUATION | BREAKOUT_RETEST | RELATED_PULLBACK_OR_RETEST_NOT_EXACT_DETECTOR |
| CONSOLIDATION_BREAKOUT | CONSOLIDATION_WEEKLY, BREAKOUT_RETEST | RELATED_STRUCTURE_DIFFERENT_TIMEFRAME_AND_RULES |
| DEEP_PULLBACK_RECLAIM | BREAKOUT_RETEST | RELATED_PULLBACK_OR_RETEST_NOT_EXACT_DETECTOR |
| DIP_AND_RIP | BREAKOUT_RETEST | RELATED_LEVEL_RECLAIM_NOT_EXACT_DETECTOR |
| FAILED_BREAKOUT_RECLAIM | BREAKOUT_RETEST | RELATED_LEVEL_RECLAIM_NOT_EXACT_DETECTOR |
| FIRST_PULLBACK | BREAKOUT_RETEST | RELATED_PULLBACK_OR_RETEST_NOT_EXACT_DETECTOR |
| FLAT_TOP_BREAKOUT | CONSOLIDATION_WEEKLY, BREAKOUT_RETEST | RELATED_STRUCTURE_DIFFERENT_TIMEFRAME_AND_RULES |
| GAP_AND_GO_CONTINUATION | None verified | NO_EXACT_SOURCE_MATCH_VERIFIED |
| HIGHER_LOW_CONTINUATION | BREAKOUT_RETEST | RELATED_PULLBACK_OR_RETEST_NOT_EXACT_DETECTOR |
| HIGH_OF_DAY_BREAKOUT | BREAKOUT_RETEST | RELATED_LEVEL_RECLAIM_NOT_EXACT_DETECTOR |
| HOD_RECLAIM | BREAKOUT_RETEST | RELATED_LEVEL_RECLAIM_NOT_EXACT_DETECTOR |
| MICRO_PULLBACK | BREAKOUT_RETEST | RELATED_PULLBACK_OR_RETEST_NOT_EXACT_DETECTOR |
| MOMENTUM_REACCELERATION | BREAKOUT_RETEST | RELATED_PULLBACK_OR_RETEST_NOT_EXACT_DETECTOR |
| MOMENTUM_SQUEEZE_EXPANSION | CONSOLIDATION_WEEKLY, BREAKOUT_RETEST | RELATED_STRUCTURE_DIFFERENT_TIMEFRAME_AND_RULES |
| OPENING_DRIVE_CONTINUATION | None verified | NO_EXACT_SOURCE_MATCH_VERIFIED |
| OPENING_RANGE_BREAKOUT | ORB_VOLUME, ORB5_REFERENCE, MULTI_INTRADAY | SAME_FAMILY_NOT_RULE_PARITY |
| POST_GAP_RECLAIM | None verified | NO_EXACT_SOURCE_MATCH_VERIFIED |
| PREMARKET_CONSOLIDATION_BREAKOUT | None verified | NO_EXACT_SOURCE_MATCH_VERIFIED |
| PREMARKET_HIGH_BREAKOUT | None verified | NO_EXACT_SOURCE_MATCH_VERIFIED |
| RANGE_COMPRESSION_BREAKOUT | CONSOLIDATION_WEEKLY, BREAKOUT_RETEST | RELATED_STRUCTURE_DIFFERENT_TIMEFRAME_AND_RULES |
| SHALLOW_PULLBACK_CONTINUATION | BREAKOUT_RETEST | RELATED_PULLBACK_OR_RETEST_NOT_EXACT_DETECTOR |
| VOLUME_CONTRACTION_PULLBACK | BREAKOUT_RETEST | RELATED_PULLBACK_OR_RETEST_NOT_EXACT_DETECTOR |

## Reuse before another implementation

Existing offline adapters already cover `OPENING_RANGE_RETEST`, `TREND_VWAP_PULLBACK`, `ORB5_ACTIVITY_RUNNER_V2` and `NOISE_BAND_CLOCK_V2`. They are adaptations with their own rules and cannot inherit source win rates. No duplicate production bot is introduced. In particular, the morning ORB benchmarks cannot serve as an all-day cent scalper.

## Fast local evidence review

Run `tools/review-equity-23-repositories.ps1` at an exact reviewed revision. It reads the known `full_research_final.json` on the operator workstation in an isolated research checkout. It uses standard-library checks, reads no credentials, makes no Grok or market-data requests, and does not merge or install source into Atlas. Git fetch/worktree preparation does contact GitHub.

The compact local JSON retains all 23 families, explicit TRAIN/VALIDATION/TEST sample counts, count reconciliation, empty strategies, and mathematical bounds on target-touch/stop-first marginals. Realized net win rate and expectancy stay unavailable: the aggregate report does not provide a chronological executable trade ledger. Missing split counts never come from execution-analysis summaries. Existing TEST labels have already been examined and are not newly untouched validation.

Episode/bar file inventory is metadata-only, bounded to 10,000 directory entries and four directory levels, without following symlinks or opening SQLite. Counts may therefore be lower bounds when capped. The private report and its performance values are not committed or published. Attach the resulting JSON rather than copying a long terminal dump.

## What remains to test

The historical report is unavailable in the Codex checkout. Its actual 23-family results must be reviewed locally first. Next, inspect the available episode/bar schema and replay the same frozen candidates on chronological prices with explicit costs, entry-candle ambiguity, shared exposure and independent evaluation dates. Source review and synthetic checks are not profitability validation. No source here has independently reproduced Atlas profitability.
