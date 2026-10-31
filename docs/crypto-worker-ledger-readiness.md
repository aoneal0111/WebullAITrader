# Disconnected crypto worker and ledger readiness

This diagnostic exercises the existing crypto subprocess, parent controller,
shared capital controller, and native PAPER simulator using invented prices.
It does not start Atlas, load credentials, connect a broker, call Grok, change
strategy rules, or open installed ledgers. It uses compiled default signal
settings, not a claim about the operator's effective runtime configuration.

Run from the checked source:

```powershell
python -m app.crypto_research.worker_readiness
```

The weekend PowerShell runner also accepts `-CryptoWorker`. It checks installed
PAPER/live/Grok-review flags without changing them, runs focused regression
tests, then saves a small `atlas-crypto-worker-*.json` on the Desktop. This
switch cannot be combined with online crypto reads. The source checkout is
disposable; installed application files and its strategy baseline stay fixed.

Each of the two cases (target and stop) creates new temporary SQLite stores,
a synthetic account, and a real spawned pure signal worker. It checks:

- Disabled entry and revoked pending entry remain blocked.
- A completion older than two seconds remains blocked with no allocation.
- A synthetic entry holds shared capital; its duplicate does not add exposure.
- Closing the worker and reopening both stores retains the entry's identity
  and reconciles its durable allocation.
- With entries disabled and the worker closed, a fresh synthetic target or
  stop quote closes the local position and settles shared capital.
- Repeating protection does not create a second exit.

`simulated_fills` refers to temporary simulator journal rows. `broker_orders=0`
means no exchange/broker client or dispatch service is constructed by this path.
The simulator router is enabled briefly only for an invented local entry;
this does not enable any desktop execution setting. All handles close before
temporary-directory cleanup, including on Windows.

A passing report establishes this bounded synthetic integration check. It does
not establish strategy profitability, actual feed-to-worker replay, sustained
runtime reliability, online Grok readiness, or permission to enable execution.
The separate bar/quote probe verifies sampled real provider inputs; neither
check alone proves the installed desktop path works end to end.
