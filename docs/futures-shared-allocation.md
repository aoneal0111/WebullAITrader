# Offline shared futures allocation

`FuturesAllocationRouter` connects the futures parent paper controller and ledger
to the existing `PaperAdmissionController`. It is explicit offline composition;
Atlas does not automatically enable it, configure allocations, start workers,
call Grok, or place broker orders.

## Capital models and accounting

Existing `EntryReservation` requests retain full notional capital and structural
price-risk requirements. `FuturesEntryReservation` is a separate explicit model,
restricted to FUTURES and one to five integer contracts. It includes supplied
margin per contract, a ledger identity and the complete plan fingerprint.

The router reserves margin times quantity plus declared round-trip slippage and
fees. Loss allocation covers entry-to-stop price risk times quantity/multiplier,
plus those execution costs. Futures and full-notional requests compete for the
same configured account capital and open-loss budget under one SQLite transaction.
The equity router reconciles only Warrior/Scalper commands; it does not inspect
or release futures commands. Shared allocation totals still include all engines.
Neither an AI proposal nor controller reservation replaces the futures ledger's
quote, cash, margin, sizing, ownership, reward or risk checks.

On verified closure, durable futures net P&L is booked once. The effective capital
ceiling is configured capital plus the lesser of cumulative futures realized P&L
and zero. Losses reduce capital available to every engine in the same declared
pool. Subsequent gains may offset those losses up to the original ceiling; they
never automatically compound above it. Backdated new requests cannot use a later
settlement. Open/unrealized P&L is not counted.

These are declared offline allocation ceilings, not broker buying power, account
equity or cross-venue cash transfer. All amounts in a pool must use consistent
cash units and be deliberately allocated to it. Equity/options/crypto realized
P&L is not settled into this table in this stage; their existing account/broker
checks remain necessary. This does not combine independently funded brokerage,
crypto, or futures-clearing accounts.

## Durable ownership and handoff

One futures ledger is persistently bound to one controller identity and account
allocation; that account accepts one futures ledger. Different bindings or an
unaccounted existing futures position are rejected. Binding does not create a
budget or change the configured one. A bound ledger's new entries require the
router's local dispatch token, including after restart. Protection remains
available without that token or entry activation.

The shared reservation is written and its dispatch claimed before invoking the
local futures simulator. Successful simulator entry is acknowledged using a
`futures-paper:` reference; this is not a broker order ID. A completed explicit
local refusal releases the allocation and retains a terminal command identity.
Pre-reservation budget refusals create no command and may be reconsidered.

The controller and futures ledger use separate databases, so their handoff is
not atomic. Recovery deliberately holds uncertain allocation:

- A matching open ledger intent repairs an interrupted acknowledgment.
- A matching closed intent with durable settlement repairs an interrupted
  settlement and releases allocation once.
- No intent after a claimed dispatch is `UNCERTAIN`, not evidence that no entry
  occurred. No retry or release is inferred from absence, expiry, worker closure,
  or a timeout. New futures dispatches remain blocked pending reconciliation.
- Unsubmitted reservations, wrong fingerprints/ledger references, unmanaged
  exposure and missing historical settlements also block new futures dispatches.
- Generic flat reconciliation cannot release this margin model without its
  verified futures settlement path.

The parent protects positions before shared reconciliation and entry assessment.
Worker submit/poll uses that parent protection path too, so a protective close
can settle shared allocation after worker closure. Shared reconciliation failure
keeps allocation reserved; it does not undo an already durable protective exit.
The caller must continue polling for protection. There is no background watchdog.

This adds persistent simulator identity, binding and settlement metadata while
preserving existing futures settings and historical intents. Old closed intents
without the new settlement record cannot manufacture a settlement or release.
Binding conflicts are not silently reset; no automatic rebudgeting is provided.

## Windows verification

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-futures-allocation-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_futures_allocation.py `
    tests/asset_modules/test_admission_controller.py `
    tests/asset_modules/test_futures_paper.py `
    tests/asset_modules/test_futures_worker.py `
    tests/asset_modules/test_paper_controller_router.py `
    tests/asset_modules/test_controlled_paper_service.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Allocation tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.futures_allocation_check
if ($LASTEXITCODE -ne 0) { throw "Allocation check failed." }
```

The synthetic check reserves an equity allocation, observes a competing futures
proposal refused for shared capital, abandons the unsubmitted equity reservation,
then opens a futures simulator trade through a real subprocess handoff. Parent
protection closes it after the worker stops and releases shared allocation.
It demonstrates mechanics only, not strategy profitability or real fills.
Desktop account composition, real data/contracts/sessions, other asset execution
adapters and optional Grok coordination remain separate integration work.
