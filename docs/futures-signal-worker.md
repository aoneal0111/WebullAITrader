# Offline futures worker handoff

`FuturesSignalWorker` is a separate spawned Python process that assesses supplied
contract bars and quotes with `FUTURES_RANGE_EMA_PAPER_V1`. It has no ledger path,
credentials, broker calls, model calls, or trading authority. Atlas does not
automatically create or enable it. The ordinary `EngineWorker` remains an
observation-only process; only the specialized futures worker accepts `ASSESS`.

## Transport

Requests and replies retain the existing 8 KiB JSON envelope limit, immutable
engine/policy/session identity, sequence checks, one in-flight request, owner
process/thread restriction and monotonic deadline. No queue, automatic restart,
retry, or arbitrary callable/code transfer is provided.

Supply at most 48 completed one-minute bars per request (the direct signal
module supports up to 256). Large frames are rejected before sending. Strict
field sets, bounded decimal strings, canonical contract symbols and aware
timestamps prevent implicit schema conversions. Only actual futures contract
fields are accepted; option fields and ledger paths are not transported.

The parent retains a snapshot of the exact bytes sent. It recomputes and verifies
the complete returned proposal, including JSON types, before accepting a reply.
This deliberately repeats evaluation for verification; this stage makes no
latency or throughput improvement claim. Worker output cannot invent prices,
settings, identity, sizing or authorization.

## Parent authority

`FuturesWorkerController` binds a futures worker to the dedicated offline
`FuturesPaperController`. Its caller must poll frequently with a current aware
decision time and current actual-contract quotes.

- Every submit/poll protects existing positions first, including while the
  worker is starting, busy, failed or closed. Missing quotes remain unresolved.
- A completion must be no more than two seconds old and cannot precede the
  submitted time. Changing signal settings while pending invalidates it.
- The parent recomputes the proposal against current quotes and invokes its
  trusted deterministic authorization callback. The old child plan is never
  submitted directly. Current trigger, extension, quote, margin and risk checks
  still apply, and entries disabled while pending remain disabled.
- Detected worker failure or closing the bridge disables new ledger entries,
  drops the pending completion and never resubmits it. Protection can continue
  through parent polling after worker closure; the bridge does not close the
  ledger or cancel protection.
- Restart requires a new worker handle/session and explicit ledger activation.
  Successful signal identities in the ledger prevent rebuying a used signal
  bar even across worker/ledger restart.

This caller-polled bridge is not a watchdog. No protection evaluation can occur
if the parent/caller stops running. Supplied quotes, real contract specifications,
exchange session/halt checks, worker supervision in Atlas, shared account
allocation, GUI wiring, broker routing and optional Grok remain separate work.

## Windows checks

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-futures-worker-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_futures_worker.py `
    tests/asset_modules/test_engine_worker.py `
    tests/asset_modules/test_futures_signals.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Worker tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.futures_worker_check
if ($LASTEXITCODE -ne 0) { throw "Worker handoff check failed." }
```

The smoke check uses a temporary synthetic ledger and fabricated bars/quotes.
It verifies a separate process, default-off entries, explicit local activation,
and a parent protective exit after the worker closes. It makes no strategy
performance or executable-fill claim, launches no Atlas GUI and requests no data.
