# Offline crypto signal worker

`CryptoSignalWorker` evaluates one bounded spot-USD history in a separate spawned
Python process. `CryptoWorkerController` owns a caller-polled handoff to an explicit
`CryptoAllocationRouter`. It is not automatically installed in the desktop or
combined with the AI supervisor. Constructor entry permission is off; the caller
must explicitly enable the shared router for PAPER entries.

The initial research candidate reuses Atlas's completed 20-bar range breakout
with 8/21 EMA agreement. It supports spot BUY only, not leveraged shorts or futures.
A structural stop comes from the five preceding bars; the executable bid must
still confirm the breakout. Quotes must be no more than two seconds old and not
precede the latest completed bar. History requires at least 22 contiguous completed
one-minute bars and is capped at 48. This is a deterministic research candidate,
not a best-strategy selection or externally validated profitable system.

Sizing uses a default $200 notional cap, $250 cost-inclusive capital budget and
$25 planned risk budget. Fee and slippage assumptions must match the native crypto
ledger (default 0.1% per side each). The default target is three structural price
risk units from the modeled entry; net modeled reward must cover net modeled risk.
Current native cash, two-position, loss and spread checks remain in force. This
change does not remove the existing native 1% spread cap or implement adaptive
spread/RVOL ranking. No take-profit timing or profitability claim follows from
these assumptions.

The worker accepts only owned bars, bid/ask, timestamp and bounded settings in
an 8 KiB frame. It receives no ledger path, account state, API key, execution
permission, code, or broker client. One request can be in flight. Parent validation
compares the complete child response to the deterministic expected response,
including price and signal identity. Parent recomputation remains necessary;
process separation does not by itself establish lower total evaluation latency.

Before submission or polling, the parent runs native protection and reconciles
shared allocation. A completion older than two seconds, clock rewind, changed
policy or cost assumptions, unavailable worker or stale current quote cannot
open a position. Entry revocation during evaluation stays revoked. The parent
recomputes price, stop, target and sizing from current quotes and passes only that
new proposal to trusted shared admission. It never executes serialized child
prices directly. The signal ID is stable for a pair, policy and completed signal
bar; successful duplicates cannot add exposure. Pending dispatch uncertainty
retains its shared reservation and is never automatically resubmitted.

Closing or failing the child disables new entries. Caller polling still runs
parent protection, even after the child closes; it does not require an AI
response. Protection needs continued caller polling and fresh supplied quotes.
There is no background data fetch, automatic worker restart or market activation.

## Windows validation

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-crypto-worker-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_crypto_worker.py `
    tests/asset_modules/test_engine_worker.py `
    tests/asset_modules/test_crypto_allocation.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Crypto worker tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.crypto_worker_check
if ($LASTEXITCODE -ne 0) { throw "Crypto worker check failed." }
```

The synthetic check uses temporary ledgers, fake quotes and no network. It tests
entry gating, process handoff, shared reservation and a native stop/target exit
following child closure. Its synthetic P/L is mechanics evidence only. It does
not launch Atlas, consume Grok credits, or place broker orders.
