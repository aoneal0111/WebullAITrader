# Profit-flow repair and source candidate tests

This change adds two executable offline daily equity strategy plugins and an explicit native PAPER exit experiment. It does not claim a profitable bot, change live permissions, start Atlas, or promote an author win rate into an Atlas result. The strategies are research candidates, not rejected because their histories differ from Atlas.

## New source candidates

Exact commits and file SHA-256 values are in `app/strategy_research/source-candidates.json`. The lab independently implements the described rules rather than importing untrusted upstream Python.

| Candidate | Primary source | Author-reported evidence | Atlas test differences |
| --- | --- | --- | --- |
| RSI2_SPY_REGIME | [jballaith/Trading_Strategy_RSI_Regime](https://github.com/jballaith/Trading_Strategy_RSI_Regime/tree/64fd903b026b5455d2c325f443e219becc89f259) | Committed output: 239 out-of-sample closed trades, 65.3% wins, PF 1.345. The same output reports PF 0.747 for 2025. MIT source. | Daily mean reversion, stock and SPY above SMA200; RSI2 <5 entry, >60 exit; close-based 10% backstop. Author code uses next-open engine defaults despite README saying close. Atlas adds shared cash, uniform explicit costs and warmup; source's zero-loss RSI=50 convention retained. |
| CONNORS_RSI2 | [jayadevrana/free-pine-script-indicators](https://github.com/jayadevrana/free-pine-script-indicators/tree/705984151b871a01df4b32c41b022b940c2d203d) | README claims 254 SPY trades, 75% wins, PF 2.53; not a supplied trade ledger. MPL-2.0 source. | RSI2 <10 and price>SMA200; exit close>SMA5. Author Pine uses signal-close fills and has no stop by default. Atlas research uses next-open fills, shared cash and identical costs. Applied across supplied stocks as a separately identified adaptation. Not attached to native execution. |

Neither daily swing strategy is a cent scalper. They must not replace the minute engine silently. NostalgiaForInfinity remains a separate crypto candidate in the existing source-reproduction lab; it is not ported into an equity engine here. The prior NFI run does not become live performance or an equity win rate.

`tools/test-source-candidates.ps1` creates a detached checkout, runs checks, captures frozen 2014-01-01 through 2026-10-31 daily SIP bars (31 symbols, at most 40 requests / 64 MiB), then compares both plugins from 2023-01-01. It reads existing local Alpaca credentials without printing them. No subscription, feed fallback, dependency installation, bot or Grok calls. Capture failure preserves its summary and raw pages. Installed Atlas stays unchanged.

The daily input is `date,symbol,open,high,low,close,volume`, one adjusted daily row per symbol/date, ascending within each symbol. SPY is mandatory. Finite values, OHLC relationships and duplicate dates are checked. Raw provider pages and input hashes remain available. Missing next-session bars cancel that research fill; no forward-filled executions. Open inventory and pending final signals remain separate from closed-trade statistics.

Each candidate has an independent $10,000 wallet and $1,000 maximum allocation per position, shared across symbols. Base comparison charges 5 bps commission and 5 bps adverse slippage per side. `-FixedResearchCost` subtracts the specified actual infrastructure/model expense from each independent case, separately from trade costs. Zero is an explicit default assumption, not proof all operating costs are zero. This is a selected surviving-stock universe, not a point-in-time universe. Historical adjusted prices can be revised. No result is called an untouched holdout because the source already examined parts of the test dates. No parameter sweep is run.

## Native exit experiment

`CENT_DEFENSE_V4` preserves V3 entry logic, freshness rules, original stops, maximum hold, ownership, capture and existing trial risk caps. It changes only the profit-defense floor for newly attributed entries: a discretionary tightening must lock at least $0.05 per share relative to weighted entry. It may sacrifice smaller profitable exits and increase giveback. It does not guarantee a five-cent fill or profit. Targets and protective stops remain executable; no stop is moved down or removed.

The floor is persisted on each entry as `scalp_cent_profit_defense_floor` and recovered from that entry. An older position does not acquire the new setting merely because the runtime/config changes. Profile V4 is opt-in; no existing trial ID, deadline, attempt count or launcher is overwritten.

The installer runs the prior frozen blocker comparison for source compatibility. That comparison does **not** simulate alternate V4 exits or demonstrate better win rate. A causal quote-path comparison and forward PAPER run are still necessary. Full journal data is required to distinguish a real quote jump from delayed or misordered matching in a specific historical trade. The summary ZIP alone cannot resolve that distinction.

## Execution and Grok flow

- Stop fills retain the executable bid/ask. Stop slippage attribution now uses the stop level as its reference, so a gap through a stop is visible even when last trade equals fill. This does not subtract slippage twice or rewrite old fills.
- Removed the news worker's silent exception handling. It records allowlisted TIMEOUT / HTTP status / network / invalid-response / internal categories, never exception text, bodies or credentials. Nine earlier generic UNAVAILABLE records cannot retroactively reveal their errors.
- Grok's independent teams and collaboration remain intact. Native orders never wait for their research. More serial model calls are not added to the scalp order path. No latency improvement or Grok trading win rate is claimed.
- Software checks establish implementation behavior only. Promotion requires measured net performance after recorded expenses, drawdown, complete inventory accounting, appropriate separate evaluation and forward evidence. The user target is >60% winning closed trades **and** positive returns after costs; neither has been demonstrated by this change.
