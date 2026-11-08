# Regime recovery research — November 8, 2026

The captured weekend audit identified a shared long-only trend/deep-dip bias. This study tests four new rule families that can enter below SMA200: RSI recovery, Bollinger reentry, drawdown recovery and range reclaim. It does not modify the running NFI or other paper workers.

## Frozen design

96 parameter combinations per timeframe, at 1h and 4h: 192 new variants. Three existing rule-level controls per timeframe bring the total to 198 configurations. Two periods and two fee scenarios produce 792 screening runs. Development: October–December 2023. Validation: April–June 2024. July–September 2024 is reserved and remains unopened because no candidate passed selection.

The six Binance USDT markets are BTC, ETH, SOL, XRP, DOGE and LINK. Public hourly candle files are verified against commit `553525cf23aa1af1155aa64d071ab1ddd5f940c1` using the existing source lock's byte counts and Git blob identities. Four-hour candles use complete groups of four aligned hourly candles; missing groups are omitted rather than invented. Period coverage is reported separately. The protocol binds both implementation hashes before the run.

| Setting | Values |
| --- | --- |
| RSI recovery threshold | 25 / 35 / 45, crossing upward with rising close |
| Bollinger reentry width | 1.5 / 2 / 2.5 standard deviations, recovering inside the lower band |
| Drawdown recovery | 3% / 6% / 10% below the prior 24-bar high, with rising RSI and close above prior high |
| Range reclaim | Prior low sweep, reclaim EMA10, RSI at least 40 / 45 / 50 |
| Stop distance | 2.5 / 4 entry ATR |
| Target distance | 2 / 4 entry ATR |
| Time exit | 24 / 72 hours |
| Entry cost check | Target distance covers twice fee/slippage plus 0.5% surplus |
| Signal exit check | Estimated net profit at signal close at least 0.5%; stops/timeouts bypass this |
| Fee scenarios | 0.4% / 0.8% per side, assumptions rather than verified account fees |
| Slippage | 0.05% per side |
| Simulated wallet | 10,000 USDT, six slots, long-only, no leverage |
| Additional cost scenario | Assumed $2 per calendar day overhead |

The research model executes signals at the next open. When a stop and target are both touched within a candle, the stop wins. The signal profit floor does not guarantee a profitable fill: the next open can gap below it. Open inventory receives an estimated terminal exit-cost deduction. Twenty-two tests passed, including prefix invariance, entry eligibility below SMA200, missing-hour aggregation, stop/timeout behavior and unchanged default replay behavior.

Selection requires at least 60 closed trades per period, 60% wins, profit factor 1.15, maximum marked drawdown 15%, at least one entry per calendar day across the six markets, entries on 60% of days, and positive estimated terminal results after the stated overhead. These are research criteria, not production order gates.

## Results

No new variant passed both development and validation. All new hourly variants lost in at least one period even before the overhead deduction. Only two four-hour variants were positive in both periods at the 0.4% fee assumption; both were slow and marginal, and failed the higher-cost/overhead checks.

Example: drawdown recovery, 10% trigger, 4 ATR stop, 2 ATR target, 72-hour timeout, at 4h:

| Metric | Development | Validation |
| --- | ---: | ---: |
| Closed trades | 16 | 23 |
| Win rate | 81.25% | 78.26% |
| Entries per calendar day | 0.20 | 0.25 |
| Net after fees/slippage and terminal inventory exit estimate | +1.08 USDT | +219.56 USDT |
| Net after assumed $2/day overhead | -182.92 USDT | +37.56 USDT |
| Net under 0.8% fee stress, before overhead | -354.30 USDT | +36.36 USDT |

This is not a qualified profitable improvement. The full score tables, protocol, source data identities and selection records are in the saved research archive. No new worker was prepared or activated.

## Limits

This is a causal OHLCV screening model, not a native Freqtrade fill validation, broker test or current Kraken USD reproduction. It does not reconstruct order books, exchange precision, cancellations or market impact. Prior exploratory research informs the grid; results do not establish future 60% wins or daily profit. The unopened holdout is not reported as passed. Existing strategy controls here are rule-level controls, not full reproductions of all seven deployed workers.
