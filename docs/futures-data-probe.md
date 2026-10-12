# Read-only futures data probe

`python -m app.asset_modules.futures_data_check` is offline by default and makes
zero SDK factory calls or data requests. It neither creates a ledger nor starts
Atlas, enables trading, subscribes to a stream or calls Grok.

Explicit `--online` uses the existing configured market-data credentials and
endpoint. With no symbol it makes two discovery attempts: MES and MNQ monthly
contracts. It does not select a front month, infer tradability, or query prices
for an automatically chosen contract. Continuous symbols are excluded.

After discovery, `--online --symbol ACTUAL_CONTRACT` makes at most three probe
attempts: instrument profile, level-one depth, and 48 one-minute bars requesting
real-time data. Substitute an actual MES/MNQ expiry symbol from the response.
There are no probe retries; initialization failures consume the attempt budget.
The counter measures probe operations, not SDK internal authentication traffic.

## Verified installed SDK calls

The 2.0.14 SDK source exposes these `DataClient` methods:

- `instrument.get_futures_instrument_by_code(code=..., category='US_FUTURES', contract_type='MONTHLY')`
- `instrument.get_futures_instrument(symbols=..., category='US_FUTURES')`
- `futures_market_data.get_futures_depth(symbol=..., category='US_FUTURES', depth=1)`
- `futures_market_data.get_futures_history_bars(symbols=..., category='US_FUTURES', timespan='M1', count='48', real_time_required=True)`

Current public documentation uses newer endpoint naming; this probe does not
upgrade the SDK, rewrite endpoints, change credentials or assume compatibility.
Webull's [futures data overview](https://developer.webull.com/apis/docs/reference/futures-market-data/)
states that futures OpenAPI data requires a paid market-data subscription and
describes that subscription module as under development. Access must be checked
against the actual response; this probe does not purchase a subscription or
infer API entitlement from mobile-app access.

## Evidence boundaries

All results retain `UNVALIDATED_RESPONSE_SCHEMA`, including successful captures.
A captured response does not authorize quotes, validate timestamps/expiry,
establish actual margin or fees, populate paper positions, or feed a worker.
Instrument specifications, session/halt rules, fresh executable bid/ask provenance,
minute-bar completion and access entitlement remain necessary before an adapter.

Responses are bounded to 128 KiB, schema depth six, 512 visited nodes, the first
64 fields per object and two samples per array. Selected numeric/time fields and
supported contract identities are retained; arbitrary provider strings and
sensitive fields are removed. HTTP error bodies and exception messages are not
printed. Typed SDK status/code diagnostics use the existing sanitized policy.
Options retains its existing sampler, calls and limits; futures uses its own
schema sampler and refuses inherited option snapshot operations.

## Windows checks

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-futures-data-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_futures_data_probe.py `
    tests/asset_modules/test_options_data_probe.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Data probe tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.futures_data_check
if ($LASTEXITCODE -ne 0) { throw "Offline futures check failed." }
# Optional explicit read-only discovery using existing credentials:
& .\.venv\Scripts\python.exe -m app.asset_modules.futures_data_check --online
Write-Host "FUTURES_DATA_EXIT=$LASTEXITCODE"
```

Discovery returns only a bounded sample of contract identities. Inspect the
profile before choosing an expiry for the subsequent schema check; the probe
does not assume provider ordering selects the nearest or active contract.
