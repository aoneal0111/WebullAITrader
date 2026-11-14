# Independent paper candidates alongside NFI and the existing crypto fleet

The add-on runs the hourly crypto ROC pullback and two daily equity source adaptations without routing their entries through Warrior, Scalper, Grok approval, the retired equity replacement gate, or the old eight-hour trial. It does not stop, reset, replace, merge wallets with, or resume existing NFI/crypto workers. No live trading credentials or broker write API are used. Atlas's window does not open: Docker runs two detached workers.

| Worker | Strategy | Execution model | Entry sizing |
| --- | --- | --- | --- |
| Crypto | ROC3_PULLBACK:0.08, 1h, 12 tested USD coin pairs | Native Freqtrade 2026.9 spot dry-run; public Kraken order book; native order/fill lifecycle | Independent 10,000 USD paper wallet; native available balance, six concurrent slots |
| Equity | RSI2_SPY_REGIME daily, 12 ETFs with SPY as benchmark | Separate SQLite simulated orders at fresh observed public regular-session prices | Independent 10,000 USD wallet, 1,000 USD stakes from the tested source adaptation, integer shares |
| Equity | CONNORS_RSI2 daily, same ETF universe | Same explicit public-price simulation model, independent wallet | Separate 10,000 USD wallet, 1,000 USD stakes |

Equity fills are public trade-price proxies with modeled fees/slippage. They are not executable NBBO quotes, broker acknowledgements, or claims that the historical next-open price was available when a worker started. Both equity candidates are daily swing tests; they do not create all-day scalping signals. The worker excludes today's forming daily candle and uses the latest completed input. It can trade only during the provider's regular trading period with a fresh timestamp. Weekends and holidays do not produce fabricated fills. Dividends are not separately credited to forward equity wallets. Forward public-price execution differs from the uniformly adjusted historical research proxy.

The crypto wrapper uses the exact broad research indicators and hourly entry rule (three-bar return below -8%, price above SMA200), with a fill-anchored 3-ATR stop using ATR persisted in the native entry tag, a completed-close SMA5 exit and a ten-hour time exit. Its native executions are a forward adaptation, not replayed OHLC fills. The new worker needs 200 completed hourly bars; the original five profiles retain their own 640-bar checks. NFI retains its separate source policy.

Crypto fees are explicitly 0.80% per side for the new experiment, matching the current entry-tier taker cost stress; this candidate lost slightly at that modeled fee in the later historical test. It is included at the operator's request as a fee-sensitive experiment, not a claimed profitable current Kraken strategy. Existing NFI/fleet 0.40% configurations are not silently changed. The equity model uses 0.05% fees and 0.05% adverse slippage per side. Model/computing overhead remains outside these trading ledgers.

## Buy checks and evidence

There is no daily entry-attempt quota, eight-hour expiry or Grok budget gate in the add-on. Source signal, completed warmup, available cash, exchange minimums, valid fresh market data, duplicate-signal protection, operator pause, and healthy recording still apply. Each has a visible diagnostic reason. Removing those checks would change the experiment or create invented fills.

The equity loop records completed input hashes, raw snapshots for each distinct completed dataset, every observed price/timestamp, decisions, orders and closed realized results. Order and observation writes commit in one SQLite transaction. Open holdings are never counted as wins. Deduplication survives restart. A missing SPY benchmark prevents new regime entries; it does not block exits for existing holdings. Operator pause also allows exits. A failed provider on one worker does not disable the other worker.

Crypto retains the native fleet's raw OHLCV, candle/signal, entry-review, order-response and ledger capture. Native capture failure or stale candles cannot authorize new entries. These are explicit failures in status, rather than an assertion that an up container is healthy.

## Launch and review

Run `tools/prepare-paper-candidate-addon.ps1 -Revision <exact-sha> -StartAfterPreparation` locally to prepare, repeat disconnected tests in the pinned official image, resolve the native strategy, validate Compose and launch only provider-ready add-on workers. Preparation uses a detached review checkout; it does not merge the installed Atlas branch. Omitting `-StartAfterPreparation` prepares launchers without starting anything.

Before launch, a bounded public-only check verifies current BTC hourly data and the SPY daily feed independently. An equity market closure is a waiting state, not a preparation failure. A feed failure prints `WORKER_NOT_STARTED` with its reason. Starting a container is not a guarantee of future fresh data or a source BUY.

The printed directory contains `Start-Paper-Candidates.ps1`, `Show-Paper-Candidates.ps1`, `Pause-Paper-Candidates.ps1`, `Resume-Paper-Candidates.ps1`, `Stop-Paper-Candidates.ps1`, and `Export-Paper-Candidates.ps1`. Reuse that directory to preserve the same wallets. Preparing a new directory makes a new experiment. Stop pauses entries and retains holdings; it does not flatten them. Resume removes only the operator entry-pause files. The old NFI/fleet controls continue to manage their own workers.

## Validation and limits

Fifty-three targeted and original-profile regression tests passed using Freqtrade 2026.9. A genuine strategy-rule BUY on synthetic causal OHLC reaches a persisted native unfilled order with recording active; the private exchange write path is disabled. A separate native dry-run exchange test confirms a marketable BUY fills and an uncrossed BUY stays unfilled. Equity source-rule BUYs and exits persist, restart deduplication holds, wallets do not borrow from one another, stale/closed-market inputs refuse new entries, and exits remain possible during pause or missing-benchmark conditions. These are controlled execution contracts, not profitable live-market results.

The cloud public SPY/QQQ feed check returned 1,255 completed daily candles through 6 November 2026 and correctly identified the weekend as closed. A cloud CCXT public Kraken check could not complete because of a network transport error; current Kraken access is therefore checked again on the user's workstation before the new crypto container starts. Windows PowerShell and Docker Desktop are unavailable on the cloud host; the local installer repeats native checks in the exact pinned Docker image. No forward orders or paid model requests were made during cloud testing.
