# Daily activity and net profitability: 7 November 2026

The operator's updated objective is an active paper portfolio with positive net expectancy after costs, rather than a rarely trading strategy selected mainly by win percentage. Profit on every day is not promised. More signals without a profitable execution model do not satisfy the goal.

The new acceptance checks require at least one filled entry per day on average, entries on at least 60% of eligible days, 60 closed trades per evaluation segment, profit factor at least 1.15, maximum drawdown at most 15%, and positive marked net after an explicit $2/day overhead sensitivity. Equity uses trading sessions; crypto includes every calendar day. The $2 is a modeling assumption, not a measured Grok invoice. Win rate, positive marked days, worst day and open holdings are reported separately. A high win percentage alone cannot pass.

## Completed research

- Crypto: six predefined coins, 5m and 15m, 15 configurations per timeframe. Fixed development January–February 2024; validation March 2024; April reserved for selected cases only. Three fee scenarios per side: 0.80%, 0.40%, 0.10%, plus 0.05% adverse slippage per side. The 0.10% scenario is lower-cost sensitivity, not assumed current entry-tier Kraken pricing.
- Cost-aware crypto followup: six fixed 15m breakout/dip plans with 2%, 3% or 4% profit targets, half-target stop distances, and at most a day of holding. This was explicitly exploratory after the first screen. Fee scenarios and validation period were retained. No failed holdout was used to choose replacements.
- Equities: 31 declared current liquid stocks/benchmark, including SPY. Fixed existing RSI2 regime and Connors source adaptations, plus the existing RSI2-fast rule. Development 2019–2021, validation 2022–2023; 2024 onward reserved for selected cases. Fees 5/10bp plus 5bp adverse slippage per side. Source candidates use tested $1,000 stakes; RSI2-fast uses six slots with one-sixth available marked equity.

Crypto candle inputs were verified by Git blob identity and byte length at `DigiTuccar/HistoricalDataForTradeBacktest` commit `553525cf23aa1af1155aa64d071ab1ddd5f940c1`. Fifteen-minute bars were separately derived only from complete groups of three five-minute bars. This is Binance USDT research history, not current Kraken USD execution qualification. Equity inputs are public Yahoo daily OHLC scaled uniformly by adjusted-close ratio: a total-return research proxy, not actual historical executable prices. Both universes have selection/survivorship bias. The sample does not establish performance in every regime.

The initial crypto screen allowed a depleted wallet to generate tiny hypothetical entries. Its failed results are retained, but its activity counts are superseded by a separate size-aware run with a $10 research order floor. This is not a verified historical exchange-specific minimum. The live native workers already enforce exchange limits. No existing paper wallet or strategy was changed.

The size-aware and followup studies performed 228 cost/segment replays: 180 regular crypto, 36 cost-aware crypto, and 12 equity. An earlier 180-run superseded screen is not counted as new independent evidence. No configuration passed both development and validation activity/profit checks. Frozen selections therefore contain no promoted case, and no holdout return was computed. This does not prove that all intraday strategies are unprofitable.

## What the numbers show

All dollar figures are simulated, based on a $10,000 initial wallet. Marked net includes open-position marks, after modeled fees/slippage and before the separate overhead sensitivity.

| Validation case | Activity | Win rate / profit factor | Marked net | Interpretation |
| --- | --- | --- | ---: | --- |
| Crypto 15m volume breakout 1.2, 0.80% fee per side, March 2024 | 13.94 entries/day | PF 0.13 | -$7,056 | More orders, severe losses |
| Same crypto case, 0.10% fee per side | 13.94 entries/day | PF 0.59 | -$2,012 | Lower costs alone do not create an edge |
| Crypto 15m breakout plan, 4% target / 2% stop, 0.10% fee | 6.06 entries/day; entries on 97% of days | PF 0.72 | -$1,077 | Wider targets still failed validation |
| Equity RSI2 regime, 31-symbol universe, 2022–2023, 5bp fee | 0.26 entries/session; entries on 14% of sessions | 61.7% wins; PF 1.16 | +$236 | Positive trading result, insufficient activity and overhead margin |
| Equity Connors RSI2, same period/cost | 0.62 entries/session; entries on 32% of sessions | 62.9% wins; PF 1.07 | +$235 | High win percentage does not meet the daily portfolio goal |
| Equity RSI2-fast 20, same period/cost | 0.74 entries/session; entries on 40% of sessions | 58.2% wins; PF 0.95 | -$448 | More active, lost money |

Kraken's fee page updated 30 October 2026 lists 0.40% maker and 0.80% taker at entry tier, with lower rates depending on volume/assets. The actual account tier is not known here. Marketable limit orders need not earn maker pricing. A 0.80% per-side taker assumption needs a price move above roughly 1.6% round trip before slippage to break even, which makes tiny-target scalping particularly difficult. This cost finding is separate from the current bots' absence of BUY signals.

## Implementation and decision

`intraday_activity_lab.py`, `intraday_cost_plan_lab.py`, and `equity_activity_lab.py` are offline evaluators. They freeze selected identities before any held-out calculation, retain failed studies, and measure day-level results and actual simulated entry frequency. Existing next-open, shared-cash, stop-gap and stop-first ambiguity handling is reused. The broad engine's optional minimum-notional argument defaults to zero for existing callers. The source-candidate evaluator's optional curve export defaults off, preserving its existing output contract.

Twenty-one targeted tests passed, covering active signal prefix invariance, no warmup trades, idle-day accounting, equity weekend exclusion, dust-sized order refusal, unchanged default source results, cash/fee accounting, stop gaps and unfavorable intrabar ambiguity. Full desktop/Freqtrade/Windows tests were not run for these offline-only changes.

No active strategy was loosened, no worker restarted, no fee changed in existing configs, and no order or paid model request was sent. A daily-profit bot has not been achieved by this study. The next candidate must demonstrate both a cost-compatible edge and portfolio activity before replacing the quiet setups; adding a known losing fast configuration only to obtain BUY orders would misstate progress.

Sources:
- https://support.kraken.com/articles/cross-platform-fee-tier-changes
- https://github.com/DigiTuccar/HistoricalDataForTradeBacktest/tree/553525cf23aa1af1155aa64d071ab1ddd5f940c1
- Existing pinned source adaptations: `app/strategy_research/source-candidates.json`
