# Options shared PAPER allocation

`OptionsAllocationRouter` binds one explicitly selected `OptionsPaper` ledger to
one configured `PaperAdmissionController` account allocation. Its optional
injection into `OptionsPaperController` also works through `OptionsWorkerController`.
It is not automatically installed in the Atlas desktop, does not call a broker,
and does not resolve the current options/futures price-data HTTP 403 responses.

## Premium and risk reservations

`OptionsEntryReservation` preserves full-notional capital requirements. It only
accepts owned long standard 100-multiplier options with one to five integer
contracts. The router reserves planned premium times multiplier and quantity,
plus modeled entry/exit slippage and both fees. This conservative capital
buffer is slightly larger than the local ledger's cash needed for entry and
exit fees. Risk reserves the premium stop distance plus both slippage and fees.
It never substitutes the futures margin model for paid option premium.

The reservation competes with Warrior, Scalper and futures commands in the
same explicitly configured account allocation. Different account IDs do not
share budgets. Broker buying power, venue transfers, realized equity/crypto P&L
and real account balances are not inferred from this simulator.

Verified options and futures settlements now contribute to the same realized
P&L adjustment: configured capital plus the lesser of combined derivative P&L
and zero. Losses reduce the ceiling; gains can offset losses but cannot grow
allocation beyond the original configured capital. Existing reserved commands
are retained if a loss lowers the ceiling; new reservations can be refused.
Shared open-loss budget and each ledger's final cash/risk checks still apply.

## Durable dispatch and recovery

The local ledger persists its UUID, allocation binding and closed-intent
settlements. New bound-ledger entries require the router's local capability;
protection does not. Existing matching open exposure can be recovered, but
unmanaged standalone positions cannot be silently adopted into the pool.

Before local dispatch, reserve then claim the shared command. A matching local
open intent is acknowledged with an `options-paper:` reference. Completed known
local refusals release the command into terminal REJECTED state. Exceptions
leave SUBMITTING reserved: absent local evidence is UNCERTAIN, not proof that
nothing happened, and is never automatically retried or released.

Reconciliation matches ledger UUID and the entire serialized-plan fingerprint.
Durable open evidence recovers acknowledgment; closed evidence books net P&L
and releases allocation once. Generic flat reconciliation cannot release typed
option or futures allocation without its settlement. Changed settlement values,
wrong bindings, missing settlement records and identity conflicts require
reconciliation. New commands cannot backdate before the latest derivative
settlement. Cross-database dispatch is not atomic; conservative recovery is
intentional.

Existing options ledgers gain additive metadata and a settlement table. Existing
positions and journal rows are preserved. Old closed standalone trades without
shared commands are not automatically imported into shared P&L. Reopening never
enables entries. Parent protection and settlement continue after worker closure,
as long as the caller continues supplying fresh quotes and polling.

## Windows checks

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-options-allocation-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_options_allocation.py `
    tests/asset_modules/test_futures_allocation.py `
    tests/asset_modules/test_admission_controller.py `
    tests/asset_modules/test_options_paper.py `
    tests/asset_modules/test_options_worker.py `
    tests/asset_modules/test_paper_controller_router.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Options allocation tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.options_allocation_check
if ($LASTEXITCODE -ne 0) { throw "Options allocation check failed." }
```

This uses temporary ledgers and fabricated quotes. Equity reservation blocks an
option entry; abandoning that unsubmitted reservation lets the option open.
Parent target protection after worker closure releases the shared reservation.
The original configured ceiling remains unchanged by the synthetic gain. This
is mechanics evidence, not a profitability or actual fill claim.
