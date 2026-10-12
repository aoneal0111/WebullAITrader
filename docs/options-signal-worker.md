# Offline options proposal worker

`OptionsSignalWorker` runs the existing options range/EMA proposal evaluator in
a spawned Python process. It receives supplied SPY/QQQ underlying history,
underlying and option quotes, explicit option contract metadata and immutable
signal settings. It receives no ledger path or entry permission and invokes no
network, broker, GUI or Grok. This stage does not enable market-data access or
wire the worker into the running Atlas desktop.

## Bounded transport

The existing `EngineWorker` session/engine/policy/sequence checks, one in-flight
request, 8 KiB frame limit and caller-polled deadlines remain in force. Options
requests accept at most 48 completed bars and eight supplied contract
alternatives. Other quote keys, duplicate contracts, unknown fields, unbounded
numeric values, nonfinite decimals, naive timestamps and executable-code fields
are rejected. Missing quotes are permitted as missing evidence, not filled in.

Shared pure field decoders moved to `contract_worker_codec`; futures retains
its existing transport semantics. Generic observation workers still cannot
request strategy assessment. These workers are process boundaries, not OS
security sandboxes or independent broker accounts.

The parent validates requests and the entire worker response against its saved
wire snapshot, including proposal ranking, JSON types and prices. The child
cannot replace a plan, change quantity to a boolean or reorder alternatives.
Parent validation also computes proposals; this is not a claim that all signal
computation left the parent or that latency has improved. No latency benchmark
or profitability promotion is made.

## Parent authority

`OptionsWorkerController.submit` and `poll` protect current positions first.
The caller must poll regularly; there is no background watchdog. Busy, failed,
closed or slow workers do not remove parent-side protection, provided current
premium quotes and calls to the controller continue.

Completions older than two seconds, time rewinds and changed settings cannot
enter. A valid proposal causes the parent to recompute using the current
underlying and premium quotes, then apply the dedicated options controller's
strict authorization, default-off activation, cost-inclusive sizing and durable
signal identity. Child prices and serialized plans are not sent to the ledger.
Current quotes can change the selected alternative or cause a refusal.

Detected failure or closure disables entries and discards pending work. There
is no automatic retry, restart, code mutation or model-generated order.
Restarting a worker and reopening the ledger retains consumed successful
signals. The options ledger still has isolated cash/risk: shared allocation,
GUI composition, real data adapters and Grok review remain later stages.

## Windows check

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-options-worker-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_options_worker.py `
    tests/asset_modules/test_futures_worker.py `
    tests/asset_modules/test_engine_worker.py `
    tests/asset_modules/test_options_signals.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Options worker tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.options_worker_check
if ($LASTEXITCODE -ne 0) { throw "Options worker check failed." }
```

This spawns a temporary observation worker and uses fabricated call/put quotes
and a temporary ledger. Entries start off; explicit local demo activation opens
a position. Parent target protection works with entries disabled, including
on the last round after the worker closes. Synthetic P&L is mechanics evidence
only. No actual quotes, trading performance, live order or paid AI is tested.
