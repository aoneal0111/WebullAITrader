# Crypto PAPER quote ownership and fill ordering

Review of the existing crypto path found that it has a spot snapshot ledger,
an off-thread proposal supervisor and a separate protection thread. Its
supervisor re-reads quotes after slow AI calls and leaves proposals disabled by
default. It is not yet connected to the shared allocation controller and its
proposal/protection threads are not the spawned futures/options signal worker
protocol. Crypto research scoring does not constitute execution authorization.

Before extending allocation, this repair closes three ledger-level gaps:

- The USD quote's canonical pair must match the requested symbol, not just the
  key used to look up the quote. A BTC request cannot fill or trigger protection
  using an ETH quote placed under a BTC dictionary key.
- Execution and quote timestamps must be aware, within the existing 90-second
  quote policy and not future-dated. Execution cannot backdate before the last
  recorded fill. Full and partial exits require a quote at or after the
  position's latest recorded BUY time. This survives reopening because entry
  evidence comes from the durable journal. Missing entry evidence refuses an
  exit rather than fabricating its timestamp.
- Fill transactions claim SQLite's write lock before reading cash and positions.
  Independent connections therefore cannot both spend the same pre-fill cash.
  Journal, positions and balances roll back together if a write fails.

Protection validates quote identity and timestamps even when prices are inside
the stop/target range. It reports missing/rejected evidence and leaves positions
open. It continues independently of AI calls. This does not invent missing
prices, relax risk checks, enable entries, make paid AI calls or add exchange
order authority. Existing fees, slippage, spot-only sizing and position limits
remain in force.

Two earlier tests changed a bid/ask while retaining the old quote timestamp.
They now supply a new timestamp for the changed quote, matching the requirement
that protection use post-entry evidence. No production clock skew allowance or
staleness threshold was widened.

## Windows validation

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-crypto-ownership-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_crypto_quote_ownership.py `
    tests/asset_modules/test_markets.py `
    tests/asset_modules/test_supervisor.py `
    tests/asset_modules/test_desktop_markets.py `
    tests/asset_modules/test_market_analysis.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Crypto quote tests failed." }
```

These are local mechanics tests, including restart, partial-exit, concurrent
connection and interrupted journal-write checks. They do not launch Atlas,
connect market data or demonstrate profitable strategies. Durable ledger
identity, dispatch recovery and shared-allocation settlement are still needed
before crypto can use the same pool as equity/options/futures.
