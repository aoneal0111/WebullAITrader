# Read-only crypto completed-history adapter

`CryptoResearchRuntime.completed_bars(symbol, as_of=..., interval=M1, limit=64)`
exposes a bounded immutable snapshot of already retained normalized provider bars.
It does not make history requests, start discovery, create candles or write a
capture file. The runtime's existing writer now uses the same lock as this reader.
Bars are eligible only when their close, observation and evidence-cutoff times
are all at or before the requested observation cutoff. Old bars first observed
later cannot be used as though Atlas knew them earlier.

`crypto_history_adapter.snapshot(runtime, symbol, now=...)` combines retained
history and the latest research quote into a `CryptoHistoryFrame` suitable for
explicit assessment by `CryptoSignalWorker`. The adapter itself has no order,
ledger, broker, AI or worker-start authority. It copies only the latest 48 actual
provider bars into the worker's bounded input and retains provider and availability
metadata in its coverage report.

READY requires a canonical spot USD pair, one provider and provider symbol,
M1 completion, at least 22 strictly ordered contiguous completed bars, current
history, and a matching quote at most two seconds old. Provider alias mismatches,
future evidence, precompletion observations, duplicate bars, missing minutes and
stale quotes prevent readiness. Missing minutes are not filled, M5 bars are not
split, and quote timestamps are not refreshed by reading. Mixed provider series
cannot be silently combined.

## Data availability

The research runtime already fetches completed bars through its existing bounded
intelligence history path when that path is enabled and configured. This accessor
does not enable it or add requests. A runtime that has no retained M1 evidence
reports NO_COMPLETED_PROVIDER_BARS. Snapshot-only JSONL captures do not establish
complete minute OHLC coverage and cannot supply this worker's bar input by
conversion alone. Retained history is in memory and is not restored by this new
accessor after process restart. Persistent provider-bar export/replay is a later
step.

The existing research snapshot cadence may leave quotes older than the worker's
two-second entry requirement. Readiness checks preserve that requirement; this
adapter does not establish a real-time feed, entitlements or executable fills.
The worker and desktop entry paths remain opt-in and unchanged. The frozen research
decision's execution_authorized flag remains false; a READY frame is input coverage,
not trading permission or demonstrated profitability.

## Windows validation

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-crypto-history-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_crypto_history_adapter.py `
    tests/asset_modules/test_crypto_worker.py `
    tests/crypto_research/test_completed_bar_runtime.py `
    tests/crypto_research/test_runtime.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Crypto history adapter tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.crypto_history_check
if ($LASTEXITCODE -ne 0) { throw "Crypto history check failed." }
```

The check normalizes synthetic provider rows, adapts them and runs one real worker
subprocess assessment. It makes zero network requests and zero orders, uses no
API credentials, and does not launch Atlas. A passing check validates mechanics,
not actual data access, trading performance or production readiness.
