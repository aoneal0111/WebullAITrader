# Separate Grok-managed Freqtrade dry-run lab

Reviewed October 27, 2026. This provides a concrete bot experiment while Atlas equity quote diagnosis and strategy qualification continue. It does **not** remove Atlas's `QUALIFIED_ONLY` gate or claim that equity trading is restored.

## Repository choice and evidence

| Candidate | Fit and decision |
|---|---|
| [Freqtrade](https://github.com/freqtrade/freqtrade), release 2026.9 | Selected independent runtime: real-time dry-run trading, dedicated SQLite history, native stops/exits and public exchange data. Runs in a separate container, not Atlas's Python environment. |
| [NostalgiaForInfinity](https://github.com/iterativv/NostalgiaForInfinity), commit `2884d15885a6996445861ce204068d1908761b5a`, X8 v18.0.163 | Selected experimental crypto signal source. Source and GPL-3.0 license bytes are pinned. The author has published positive simulated results, but those results have not been independently reproduced here. |
| [Backtesting.py](https://github.com/kernc/backtesting.py) | Useful lightweight independent historical checker. It is a backtest engine, not the forward-running bot chosen for this experiment. |
| [LEAN](https://github.com/QuantConnect/Lean) | Substantial multi-asset engine. Moving Atlas equity execution into it would be a larger integration and data task. |
| [Jesse](https://github.com/jesse-ai/jesse) | Crypto framework; its official live/paper plugin has separate licensing. No new paid product selected. |

The [author's Binance Spot summary](https://iterativv.github.io/NostalgiaForInfinity/backtest-results/binance-spot/summary/) reports 289 trades, 96.95% average win rate and 3,123.95 USDT profit from a 10,000-USDT simulated wallet. It labels the environment as a backtest and includes an aggregation note; it does not establish independently audited live performance, a reproducible Atlas result, or the performance of the current pinned X8 code. Do not compare that number directly with Atlas's equity lifecycle win rate.

The inspected X8 source has a -99% fallback stop and can veto ordinary stop-loss exits. Its pair breadth, multiple timeframes and position-management assumptions differ from Atlas. Therefore we do **not** present the original as an approved Atlas replacement. This lab uses a separately named adaptation, `ATLAS_NFI_CAPPED_DRY_RUN_V1`:

- BTC/USD and ETH/USD on Kraken public data, spot long only, five-minute native signals.
- 1,000 virtual USD starting wallet; at most two positions and at most 50 virtual USD stake per position.
- Fixed -2% native stop setting, no position adjustment/averaging down, no shorting/leverage. Gaps, fees and simulation behavior can make a loss exceed the nominal stop.
- Simulated fee assumption 0.4% per side, not a verified user fee tier or Webull fill model.
- No native exit veto; Grok can never stop protective/manual exits.

These changes alter the strategy substantially. **The author's published performance does not apply to this adaptation.** Neither short tests, a high closed-trade win rate nor a successful startup qualify a policy for Atlas. The two-pair experiment is a small operational trial, not a reproduction of NFI's broad pairlist backtest.

## What Grok manages

The manager uses the existing local xAI key/model through Atlas's bounded Responses API client. It receives only the dedicated dry-run ledger's aggregate closed-trade count, realized net, open count/stake and fixed experimental limits. It does not receive Atlas accounts, historical production trades, raw keys or private configuration.

Grok may return only `ALLOW` or `PAUSE` with a reason. An ALLOW lease expires after ten minutes. The native strategy still needs its own signal and exchange/pricing checks. Grok cannot create an entry, change size/prices/stops, change code, authorize a real order or veto an exit. This slower experimental management scope avoids treating a 15–25 second model response as a fresh scalping quote decision.

The manager performs at most four paid inference attempts, roughly five minutes apart over fifteen minutes. Missing usage is not zero cost. Invalid/late/failed responses stay paused; loss/exposure checks run before inference and again afterward. A realized loss of 20 virtual USD or excess position/stake count blocks further model-driven entry permission. Open unrealized PnL is explicitly unknown in this first ledger-only manager; it cannot infer total portfolio drawdown from realized PnL.

Manual `Pause-Entries.ps1` creates a persistent pause marker. An in-flight model completion and subsequent cycles cannot override it. Starting the manager script again explicitly starts a new trial and clears that marker after acquiring the single-manager lock. At manager completion, entry permission is revoked; Freqtrade must stay running to manage residual virtual positions. It may legitimately take no trades when native signals are absent or Grok pauses.

## Installation and local procedure

Run `tools/prepare-freqtrade-lab.ps1` pinned to the reviewed integration commit. It creates a new directory under LocalAppData/AtlasResearch, checks this integration's offline tests, fetches the exact upstream commit and verifies strategy/license SHA-256 values. It generates a dedicated configuration, virtual ledger location, Docker Compose JSON, source manifest, and three manual scripts. It never starts Atlas or Freqtrade and never makes Grok calls during preparation. No subscription or exchange account/API key is required for the dry-run setup.

1. Docker Desktop must be installed/running with Linux containers. The generated launcher checks this rather than installing/changing the computer silently.
2. Run the printed `MANUAL_DRY_RUN` script. It verifies frozen files, pauses entries, and runs a network-disabled check inside the pinned container before starting the native bot. The image is pinned by digest. First use downloads the image and public exchange candles; multiple-timeframe warm-up can take time.
3. Run `MANUAL_GROK_MANAGER` in a second PowerShell window. It reuses local xAI configuration without asking for the key. Keep `ATLAS_GROK_PAPER_ENTRIES_ENABLED=false`; that setting is not a lab switch.
4. Use `MANUAL_PAUSE_ENTRIES` to stop new lab entries without disabling native exits. Keep the dry-run worker running for any residual positions. Stop the worker normally when done and inspect its virtual ledger before restarting.

No exchange credentials, Atlas `.env`, Atlas databases, broker session, Docker socket, or listening host port is mounted into the bot container. The native bot has public market-data access only through its empty-key dry-run configuration. The manager runs outside the container with the existing xAI client; API server and Telegram are disabled. Source/config verification stops on changes instead of rewriting the installed baseline.

Private experiment trades and Grok reasons remain local. `manager-summary.json` contains request/usage/diagnostic counts, not trading performance. Do not upload the dedicated trade database or private manager results to GitHub.

## Verification and remaining readiness

Local checks load the actual pinned NFI source with Freqtrade 2026.9, validate Freqtrade's configuration schema, verify the stop/stake settings and exit callback, and forbid network during that check. Disconnected tests cover malformed/expired permissions, live-mode rejection, no extra order authority, state changes during inference, manual pause races, loss/exposure limits and separate read-only ledger access.

Docker is unavailable in the Codex environment, so the container launch and Windows PowerShell execution are not claimed as tested here. The cloud host's attempt to read Kraken markets failed with ExchangeNotAvailable; local public-feed connectivity, pair availability and continuous warm-up must be verified by the generated launcher and native startup logs. No online Grok request or forward dry-run trade has been executed by Codex. No profitability result has been produced.

Atlas's separate five-minute paired stream/REST capture remains the next evidence source for its stale confirmation issue. After reliable quotes, each equity candidate still needs a connected native route and matching reviewed evidence before approval. This lab supplies parallel simulated trading; it does not bypass those repairs.

Official references: [Freqtrade Docker](https://www.freqtrade.io/en/stable/docker_quickstart/), [configuration / dry run](https://www.freqtrade.io/en/stable/configuration/), [lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/), [NFI setup](https://iterativv.github.io/NostalgiaForInfinity/installation-and-setup/), [Jesse live/paper plugin](https://docs.jesse.trade/docs/livetrade).
