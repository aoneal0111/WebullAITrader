# Optional crypto supervisor allocation

`CryptoSupervisor(paper, source, provider, entry_router=router)` now accepts a
`CryptoAllocationRouter` owning that exact paper ledger. Construction disables
router entries. `entries_enabled` requires an explicit boolean and synchronizes
the router gate. Closing the supervisor revokes entry permission.

BUY proposals go through shared capital and loss-budget admission, trusted
local authorization, and the durable crypto handoff. Quotes are read again after
the model call and dispatched with the current time; the shared two-second quote
freshness rule remains in force. Model calls never hold the entry gate.

The independent protection thread uses native stops and targets, then reconciles
shared allocation. The same operation runs before each supervisor tick, including
when entries are disabled or AI is unavailable. AI SELL proposals use the native
ledger exit path and then reconcile allocation. Partial exits retain the full
reservation until final settlement. Unknown dispatch exceptions are reported as
requiring allocation review rather than being described as rejected orders;
unresolved allocation blocks further crypto buys and does not auto-retry.

This is optional PAPER composition. The existing desktop constructor remains
unchanged and does not automatically attach a shared router or enable entries.
The caller must configure the account pool and construct the router with a
trusted authorization callback. There are no new paid API calls, broker orders,
AI credentials, or changes to Grok configuration. Tests use fake model proposals
and synthetic quotes; they do not establish trading profitability.

## Windows validation

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-crypto-supervisor-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_crypto_supervisor_allocation.py `
    tests/asset_modules/test_crypto_allocation.py `
    tests/asset_modules/test_crypto_durable_intents.py `
    tests/asset_modules/test_crypto_quote_ownership.py `
    tests/asset_modules/test_supervisor.py `
    tests/asset_modules/test_desktop_markets.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Crypto supervisor tests failed." }
```

Coverage includes shared equity competition, default-off activation, router
ownership, revocation during model calls, fresh execution quotes, exactly-once
exit settlement, uncertain dispatch holds, and protection while AI is blocked.
No Atlas process is launched by this command.
