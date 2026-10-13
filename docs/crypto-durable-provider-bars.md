# Durable crypto completed-provider-bar capture

`ATLAS_CRYPTO_COMPLETED_BAR_CAPTURE_ENABLED=true` selects a dedicated capture at
`<crypto discovery file directory>/crypto-completed-bars.sqlite3`. The setting
is false by default. Construction is lazy: no capture file is created until the
existing history path supplies an actual normalized completed-bar batch. The flag
does not activate crypto, start Atlas, request new data or enable entries.

History acquisition still requires the existing crypto research/intelligence
path. In desktop configuration this includes `CRYPTO_INTELLIGENCE_RESEARCH_ENABLED`;
this capture flag does not turn that path on or alter its request budgets.
No source quotes or simulated trades are written to this database.

`CryptoBarCapture` stores normalized provider OHLC, volume, pair/provider identity,
interval, open/close times, source observation/cutoff and first durable capture
time. It accepts up to 64 bars per batch, validates bounded fields, and commits
atomically under a SQLite write lock. Connections close after each operation;
there is no background capture worker or connection retained across shutdown.

The stable provider-bar identity includes pair, provider, provider symbol,
interval and opening time. Equivalent numeric formatting is not a revision.
Later copies of the same bar are duplicates and retain the first observed and
first durable availability times. Changed OHLC/volume values for that identity
refuse the whole batch and require review; earlier evidence is not overwritten.
Concurrent duplicate writers store one copy. Interrupted/conflicting batches
cannot leave partial new evidence.

The dedicated application marker/schema rejects unrelated SQLite databases.
Default capacity is 100,000 unique bars across all captured symbols/intervals.
That capacity is persisted and cannot be silently changed on reopen. Full
captures refuse new batches; no rotation, automatic deletion or disk-space
reclamation occurs in this version. Explicit capture rotation/migration is a
later step. Record counts are bounded, but SQLite lock or disk failures remain
possible. Capture errors increment separate runtime counters and do not suppress
normal provider admission or position protection. Writes run in the existing
research history refresh path, outside its in-memory state lock.

`read_bars(path, symbol, as_of=..., interval=M1, limit=64)` opens the file read-only,
checks evidence fingerprints, and returns at most 64 actual completed bars.
Eligibility includes the first durable capture time; a file written later cannot
be replayed as available earlier. Original source timestamps are preserved.
Readback does not seed live runtime history automatically, fill gaps, replace
quotes, authorize a worker or place an order. Captured bars alone cannot establish
fresh quote availability, fills, profitability or provider entitlements.

Runtime metrics include `completed_bar_capture_stored`,
`completed_bar_capture_duplicates` and `completed_bar_capture_failures`.

## Windows verification

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-crypto-bar-capture-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/crypto_research/test_bar_capture.py `
    tests/crypto_research/test_completed_bar_runtime.py `
    tests/crypto_research/test_runtime.py `
    tests/asset_modules/test_desktop_markets.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Crypto bar capture tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.crypto_bar_capture_check --synthetic
if ($LASTEXITCODE -ne 0) { throw "Synthetic bar capture check failed." }
```

The synthetic check verifies restart readback, first availability, duplicate
suppression and adapter readiness. It uses temporary files and makes zero network
requests or orders. It does not demonstrate real data access or performance.

A separate inspection of an existing capture uses:

```powershell
& .\.venv\Scripts\python.exe -m app.asset_modules.crypto_bar_capture_check
```

`--path`, `--symbol` and an aware `--as-of` ISO timestamp can select another saved
window. MISSING_CAPTURE is a valid observation when capture has not yet run.
BAR_WINDOW_AVAILABLE describes a bounded saved bar window; quote validation is
explicitly NOT_PERFORMED. No file is created by read-only inspection.
