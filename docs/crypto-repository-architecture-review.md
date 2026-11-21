# Crypto repository and Atlas architecture comparison

Reviewed October 25, 2026 against Atlas `0aa83d8addbec2c3ab3ec9d131d55ed1dde00550`.
Exact upstream commits, README/license blob hashes and source URLs are retained
in `crypto-repository-source-lock.json`. This is a source review, not a purchase,
framework installation, broker connection or evidence of profitable Atlas trades.

## Installed state and practical choice

Atlas uses its own native crypto research, worker, allocation and PAPER ledger
modules. Its reviewed `requirements.txt` and `pyproject.toml` do not declare
Freqtrade, Jesse, Hummingbot, NFI, Passivbot or OctoBot, and the inspected desktop
composition does not import their engines. This does not inventory arbitrary
packages elsewhere on the user's Windows machine. They have not been installed
by this review. Existing independently expressed crypto research candidates are
already present; that is different from installing the upstream repositories.

The best-fit references for this project are Freqtrade's research validation,
Jesse's separation of strategies and simulation, and Hummingbot's position
lifecycle controls. This is a compatibility judgment, not a profitability rank.
Preserve one native Webull data/allocation/execution owner. An unrelated exchange
framework cannot simply become an Atlas strategy by installing its package.

| Repository | Reviewed SHA prefix | Useful contribution | Fit and performance evidence |
| --- | --- | --- | --- |
| [Freqtrade](https://github.com/freqtrade/freqtrade) | `150a10bc2b2c` | Multi-strategy backtesting; explicit execution assumptions; lookahead and recursive/warm-up diagnostics | Strong research reference. GPL-3.0 source was reviewed, not incorporated. Exchange/config/strategy interfaces differ from Atlas. No verified Atlas win rate follows from the framework. |
| [Jesse](https://github.com/jesse-ai/jesse) | `e26778ee45d1` | Explicit strategy lifecycle, multi-timeframe research and backtest/live consistency goals | MIT framework; useful architectural reference. Its own accuracy claims are author claims. No adapter to Atlas's Webull PAPER ledger was established by this review. |
| [Hummingbot](https://github.com/hummingbot/hummingbot) | `9af100d6822d` | Separate controllers and executors; stop/target/time-limit/trailing position controls | Apache-2.0 framework; useful model for deterministic management. Source `position_executor/data_types.py` exposes triple-barrier settings. Market making/arbitrage needs a different connector/liquidity model from Atlas spot snapshots. |
| [NostalgiaForInfinity](https://github.com/iterativv/NostalgiaForInfinity) | `9c4f33c7c525` | Mature, complex Freqtrade strategy suite; explicit configuration and pairlist recommendations | GPL-3.0. README recommends 5m, 40–80 pairs and 6–12 simultaneous trades, preferring stablecoin pairs. That is materially different from Atlas's tested BTC/USD + ETH/USD path, small capital caps and two-position ledger. README points to author backtest discussions, not an independently reconciled Atlas or live record. |
| [Passivbot](https://github.com/enarjord/passivbot) | `772abadc10a5` | Shared planning between backtest/live and bounded exposure concepts | Public-domain license text. Current README describes perpetual derivatives and a default trailing-martingale strategy that can add as price moves against it. This is a poor direct fit for the current long-only spot design; no averaging-down/leverage change is adopted. |
| [OctoBot](https://github.com/Drakkar-Software/OctoBot) | `c45fdc2c95c5` | Modular strategy automation, paper simulation and operational UI ideas | GPL-3.0 framework. Grid/DCA/AI workflows and a polished UI do not establish net wins for Atlas. Running another capital/position owner would require an explicit integration design. |
| [Freqtrade strategies](https://github.com/freqtrade/freqtrade-strategies) | `f3340ce11f5b` | Reproducible examples and strategies to compare on identical data | GPL-3.0. Maintainers explicitly say results depend on pairs, timeframe and dates. Bollinger/RSI ideas already have independent research adaptations in Atlas. |
| [Jesse examples](https://github.com/jesse-ai/example-strategies) | `7c91e0a37bf6` | Small inspectable rules such as RSI2, Donchian and Bollinger | MIT examples, explicitly not ready-to-go profitable strategies. RSI2 uses SMA200 and exits above SMA5; it cannot be faithfully evaluated through a 48-bar history contract. |

The review read the pinned README and license in every row. Additional inspected
sources: Freqtrade `freqtrade/templates/sample_strategy.py` (5m sample with 200
startup candles); Jesse examples `RSI2/__init__.py`; Hummingbot
`hummingbot/strategy_v2/executors/position_executor/data_types.py`; Atlas native
signal, worker, adapter, supervisor, ledger and composition modules. A framework's
sample is not its recommended profitable strategy. No upstream source is copied
into Atlas, no auto-updater is installed and no external trading process runs.

## Architecture that is already present

| Capability | Atlas evidence | Status |
| --- | --- | --- |
| Provider timestamp and owned pair validation | `crypto_research/provider.py`, runtime, history adapter | Implemented; prior bounded BTC/ETH input trials passed, not continuous reliability certification. |
| Completed bars with causal availability | `crypto_history_adapter.py`, durable bar capture/replay | Implemented; native worker consumes 22–48 contiguous M1 bars, current within one minute. |
| Pure strategy computation in a separate process | `crypto_signals.py`, `crypto_worker.py` | Implemented and tested in a separate diagnostic path. Parent recomputes against current quotes. |
| Current executable quote at entry | `crypto_signals.propose` | Requires a matching USD bid/ask no older than two seconds and not predating the final completed bar. |
| Durable entry identity, shared budget and settlement | `crypto_allocation.py`, `crypto_paper.py` | Implemented. Shared allocation is configuration-dependent and default-off; local crypto cash and portfolio budgets are distinct concepts. |
| Stops/targets independent of AI entry permission | `CryptoPaper.protect`, supervisor protection thread, router protection | Implemented local simulator path. Disabled entries do not remove existing position protection. |
| Desktop deterministic strategy ownership | `composition/asset_modules.py` | Incomplete: composition attaches `CryptoSupervisor` with a Grok provider and research-runtime snapshots. It does not instantiate `CryptoSignalWorker` / `CryptoWorkerController` as the native desktop strategy engine. |
| Deterministic time limit, trailing profit lock and partial-exit policy | Native ledger positions contain static stop/target; native signal worker has no position-manager state | Not implemented as a versioned deterministic crypto exit controller. Model-proposed SELLs are not equivalent. |
| Comparable strategy evidence | `strategy_research/crypto_screening.py`, recorded native assessment replay | Partial: four fixed longer-timeframe adaptations exist; recorded native replay assesses signals without orders/PnL. The 6h/daily study does not rank the one-minute native policy. |

The earlier 507 passing crypto-related tests establish tested correctness
scenarios, not a measured profitable desktop strategy. Actual outage recovery,
continuous native desktop behavior and prospective net performance remain
unverified. Crypto proposal entry switches and Grok remain disabled here.

## Concrete finding from the repository comparison

Freqtrade documents [recursive analysis](https://www.freqtrade.io/en/stable/recursive-analysis/)
for indicators whose seed depends on the number of supplied startup candles.
Its [lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/)
checks a different failure: indicators/signals using future information. Both
checks matter; one does not substitute for the other.

Atlas's `contract_logic.direction` seeds EMA8/EMA21 from the first supplied close.
The native crypto adapter and worker cap that history at 48 bars. A newly added
`tools/crypto_warmup_study.py` compares native directions on 22/32/48/96/192/384-bar
suffixes ending at the same decision cutoff. It runs no external framework and
changes no indicator or entry settings.

A controlled 192-minute downturn/pause/small-breakout path produces LONG from
48 bars and no direction from the supplied full history. The actual native pure
`propose` function can emit a research BUY proposal on the 48-bar version with
an invented fresh quote; no order is created. This demonstrates seed sensitivity,
not a proven losing trade, future leakage or proof that 192 is optimal. All
positive/negative claims must remain scoped to this constructed path.

Five tests passed: native proposal/direction reproduction, constant and trending
controls, missing/future/stale path rejection, bounds and input immutability.
The diagnostic is safe to run locally as:

```powershell
& .\.venv\Scripts\python.exe tools\crypto_warmup_study.py --synthetic
```

The callable `analyze` also accepts actual typed completed bars, but their
provider availability at the decision cutoff must be established separately.
Real-history comparisons are required before changing buffer sizes or EMA seeds;
a longer in-memory buffer alone does not fix missing provider history.

## Changes in priority order

1. **Finish the native deterministic desktop path with entries off.** Feed the
   existing worker through the same validated history, parent recheck, allocation
   controller and durable ledger. Establish one strategy owner per lifecycle;
   do not let the Grok supervisor and a second framework independently buy the
   same position. Grok may later observe without becoming admission authority.
2. **Version and measure warm-up handling.** Compare 48-bar control with longer
   causal histories on actual BTC/USD and ETH/USD data. If a change is justified,
   update provider storage/read bounds, adapter and worker codec together, with
   explicit warm-up status and prefix/worker parity tests. Preserve fresh-quote,
   future-bar and missing-minute guards. Do not pretend SMA200 works with 48 bars.
3. **Run a small same-data strategy comparison.** Keep the current range/EMA
   policy as control. Independently express a trend-pullback/retest candidate and
   the existing RSI2/Bollinger research ideas on explicitly chosen supported
   timeframes. Freeze rules before outcomes; separate trend/range regimes and
   evaluate regime classification using past bars only. Do not select whatever
   backtest has the largest headline win percentage.
4. **Compare deterministic exit management.** Model stop/target/time-limit and
   optional partial/trailing variants, borrowing the architectural idea of
   Hummingbot barriers. Evaluate changing the current 3R target separately from
   entries. The ledger currently enforces at least a 2R gross target plus an
   estimated-cost allowance; a lower-target candidate needs its own versioned
   admission and cost model, not an unannounced bypass. Persist remaining
   quantity, realized cash, peak, stop and deadlines across restart before release.
5. **Judge net outcomes and deploy prospectively.** Use chronological later
   periods, fee/slippage stress, worse gap fills, stop-first ambiguous candles,
   all open-position liquidation marks, drawdown and shared-capital limits.
   Previously inspected dates cannot be called a fresh holdout. Count trade wins
   only after complete lifecycle settlement, not MFE or eventual target touches.
   Run a bounded native desktop PAPER observation and a real outage/recovery
   check before considering entries.

A smaller target may increase the number of wins while reducing the average
win. High closed-trade win percentage can also hide inventory losses in grid or
averaging-down strategies. The actual goal is positive net expectancy within
acceptable drawdown and exposure; no repository inspected here establishes that
for Atlas. There is no supported claim of a universal highest-win strategy.

## Release and cost impact

This review adds documentation, source locks and an offline research tool/test.
It changes no `app` source, dependencies, entry flags, ledger schema, policy
settings, subscriptions or credentials. Monday's prepared source/config receipt
remains applicable if the installed app/settings remain unchanged. No additional
Alpaca plan, xAI purchase, external bot process or Atlas launch is required.
