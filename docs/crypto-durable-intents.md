# Durable crypto PAPER dispatch and settlement evidence

This stage gives `CryptoPaper` persistent evidence needed for a later shared
allocation adapter. It does not connect crypto to the shared pool yet, enable
AI proposals, add a strategy, call a broker or start a worker.

## Identity and atomic evidence

A dedicated crypto ledger now carries an application marker and persistent UUID.
Each successful BUY stores its dispatch terms fingerprint, original quantity,
entry cost including fees, opening time and zero initial realized P&L. An active
pair is linked to that entry identity. Each successful BUY/SELL also stores a
separate dispatch record with executed quantity, entry identity and exit P&L.

Fingerprints cover action, canonical symbol and normalized trading terms:
BUY notional/stop/target, or SELL fraction. Free-text reasons and quote snapshots
are not part of the logical dispatch fingerprint. The actual execution quote
still has to pass the ledger's identity/time checks before a duplicate is
recognized. Monetary fingerprint inputs are bounded and normalization is
independent of the caller's Decimal precision.

Reusing a filled ID with identical terms returns without another fill. Changed
terms under that ID are rejected, including after reopening. Partial exits
accumulate realized P&L into the original entry and leave it OPEN. The final
exit records closure time and the whole entry's net realized result. Entry,
partial, final settlement, dispatch, journal and account changes share one
SQLite transaction; a failed journal write rolls everything back.

`intent_evidence(id)` exposes OPEN/CLOSED state, fingerprint, original/remaining
quantity, entry cost, realized P&L and settlement time. Missing or contradictory
active-position linkage is INCONSISTENT rather than proof of flat inventory.
`dispatch_evidence(id)` exposes the matching local dispatch fill. These are
simulator records, not broker fills or shared allocation permissions.

## Legacy preservation

Only the exact original four-table crypto schema can be adopted without an
application marker. Foreign schemas and other simulator markers are refused
before writes. Legacy accounts, open positions, journal and decisions remain
preserved; extra evidence tables are additive.

Legacy positions are not assigned invented entry fingerprints or settlements.
They can still be protected using their existing journal entry timestamp; their
exits update local cash/P&L, but unlinked history is not certified for shared
allocation. Reusing a legacy journal ID without verified terms is refused.
Existing missing entry evidence still prevents an imagined exit. The supervisor
continues to own entry activation; this migration does not enable it.

## Windows validation

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-crypto-intents-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_crypto_durable_intents.py `
    tests/asset_modules/test_crypto_quote_ownership.py `
    tests/asset_modules/test_markets.py `
    tests/asset_modules/test_supervisor.py `
    tests/asset_modules/test_desktop_markets.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Crypto intent tests failed." }
```

The checks cover restart identity, conflicting/equivalent terms, partial/final
settlements, later episodes of the same pair, legacy preservation, foreign-file
refusal and transactional rollback. They use local fabricated quotes and do
not launch Atlas, connect data or make paid AI calls. Shared reservation/dispatch
binding and exactly-once controller settlement remain the next integration.
