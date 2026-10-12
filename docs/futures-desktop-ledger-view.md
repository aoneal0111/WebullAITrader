# Futures saved-ledger view

The existing Futures workspace now includes a read-only view of saved simulator
positions, account balances and recorded events. The scenario calculator remains
available. This does not start a futures worker, connect a feed, activate entries,
change shared allocations, call Grok or place orders.

Desktop composition supplies a reader for `futures-paper.sqlite3` beside the
configured execution database. It does not create that file. The offline checks
use temporary ledgers that are deleted when the checks finish, so running them
does not populate this desktop path.

Use **Open paper ledger…** to select an existing futures simulator ledger. The
selection is shared across Futures routes for the current window/session; it
does not rewrite configuration. **Refresh saved ledger** rereads it. Selection
and page activation also refresh the view. No recurring database polling or
market subscriptions are added.

Positions and the general Futures routes show saved contract, direction,
quantity, entry fill, stop, target, reserved margin/risk and expiry. Orders,
Activity, Decisions and Replay routes show up to 50 recorded simulator events;
these are not broker orders, execution acknowledgments or a replay engine.

The account panel shows saved cash, realized P&L, reserved margin/risk and margin
headroom after reserved exit fees. It does not invent live marks, unrealized P&L,
broker buying power, entry-enable status or worker health. The notice includes
the selected file and read time so these records are not mistaken for live data.

## Reader boundaries

`FuturesLedgerReader` opens SQLite with `mode=ro` and `query_only=ON`, checks the
dedicated futures application marker, reads one transaction and closes the
connection. It limits position reads to the simulator's two-position cap and
events to 50; oversized plans/events and invalid ownership/numbers are rejected.
It reads only known account/position/event fields, not arbitrary metadata.

Missing files are not created. Foreign, malformed or busy ledgers leave balances
unavailable and inventory unknown rather than claiming a flat account or zero
P&L. A failed refresh clears displayed rows and labels the unavailable inventory.
This is a saved-ledger inspection feature, not a reconciliation authority.

Futures market activation remains unavailable in desktop composition. Options
keeps its existing planning workspace; it does not borrow futures records.
Equity, Warrior, Scalper and Crypto routing is unchanged.

## Windows verification

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-futures-view-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_futures_ledger_view.py `
    tests/asset_modules/test_market_analysis.py `
    tests/asset_modules/test_desktop_markets.py `
    tests/asset_modules/test_strategy_views.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Futures view tests failed." }
```

These checks verify read-only access, bounds, error states, saved-account math,
workspace routing and activation isolation. They are not strategy performance
tests and require neither broker access nor a Grok key.
