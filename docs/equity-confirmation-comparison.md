# Paired Scalper confirmation audit

The October 27 spot checks returned advancing snapshot and depth timestamps for SPY, AAPL, BRAI and CLRO. The BRAI/CLRO samples were under five seconds old; depth was not consistently fresher. They do not reproduce the earlier native Scalper confirmations with bid/ask ages of 5–50 seconds. There is no evidence here to buy a subscription or replace the quote source.

Source review finds that `WebullExecutionQuoteSource` makes a snapshot method call per confirmation. The audited client forwards calls without an Atlas snapshot cache. The scanner adapter replaces BBO on nonregressing QUOTE updates, maintains trade and quote timelines separately, and does not let trade traffic refresh an old quote timestamp. This establishes code behavior, not continuous provider reliability.

The passive capture now passes the exact immutable authorization preview to the existing asynchronous context tap alongside its returned confirmation. Before any history/context eligibility filter, the worker compares:

- Preview LAST/BID/ASK age at its original decision time.
- REST LAST/BID/ASK age at the actual confirmation evaluation time.
- Signed BID/ASK timestamp advances and preview-to-confirmation elapsed time.
- Whether BBO prices match; whether prices changed despite identical quote timestamps.

`session-summary.json` includes `scalper_confirmation_comparison`. Its fixed numeric counters distinguish STREAM_STALE_REST_STALE, STREAM_STALE_REST_FRESH, REST future timestamps and timestamps that stayed the same, advanced or regressed. No symbols, prices, credentials or trade performance are included in this comparison section. The existing private context capture retains its original behavior.

Freshness classification uses the current five-second book-age boundary and rejects any future component; old LAST alone is informational. It is not full strategy, risk or entry approval. The sequential samples cannot establish simultaneous BBO or market entitlement coverage. No samples means no conclusion.

The tuple queue remains bounded at 16. Comparison runs on the capture worker and has fixed-size numeric aggregates. No diagnostic method calls the quote source. Hook failure remains isolated from the policy result. No provider timestamp, entry permission, protective exit, strategy admission or installed file changes.

## Windows procedure

Close Atlas normally before preparing a new frozen capture. Extract and run `tools/prepare-equity-confirmation-audit.ps1` at the reviewed commit. It checks safe PAPER flags and source/configuration compatibility, runs offline tests and prints a manual capture launcher. It does not launch Atlas or make market-data/model requests during preparation. The user manually launches the printed script, clicks START normally, and sends `capture/session-summary.json` after the five active minutes. Premarket, regular and after-hours capture windows remain supported. Do not start the separate paid Grok observer.

The frozen copy uses the installed PAPER baseline with passive hooks, not the research branch's trading rules. Installed files and settings remain unchanged. New BUYs remain subject to the existing qualification gate; this audit does not qualify a strategy or demonstrate profitable execution.

Preparation also accepts the exact prior passive Scalper hook without the new `preview` argument. It upgrades only that complete, uniquely matched hook context in the frozen copy. Repeated application is idempotent; edited or duplicated hook contexts still fail closed. Regression checks cover the 50c41d9, c74a741 and d7d913a instrumented baselines as well as the original pre-hook baseline.
