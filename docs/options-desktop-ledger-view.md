# Options desktop saved ledger view

The existing Options tab now displays a bounded read-only view of a selected
`OptionsPaper` SQLite ledger. It retains the long-option expiry scenario
calculator. It does not start an options worker, enable entries, connect quotes,
call Grok, create a simulator ledger, or submit orders.

Desktop composition points its shared options reader to `options-paper.sqlite3`
beside the configured execution database, independently of the futures reader.
If no saved file exists, the view says unavailable. The prior mechanics checks
use temporary ledgers that are removed afterwards, so a missing default file
is expected until a persistent options simulator is explicitly composed.

Use **Open paper ledger…** to choose an existing saved ledger and **Refresh
saved ledger** to reread it. Refresh also runs when the page becomes visible;
there is no recurring poll. The selection is shared across options routes for
that application session and is not saved as a configuration change. No new
ledger is created by selection or refresh.

## What is displayed

Positions and general options routes display saved standard SPY/QQQ long CALL
or PUT positions: exact contract, underlying, right, quantity, premium entry
fill, stop, target, paid premium cost, reserved risk and expiry. The summary
shows cash, realized P&L, open premium cost, reserved risk and exit fee reserve.
Cash headroom subtracts exit fees only: premium was already paid and must not
be subtracted a second time. Paid premium cost excludes entry commissions;
it is not a live valuation.

Orders, Activity, Decisions and Replay routes display the last 50 recorded
simulator BUY/SELL events. These are not broker working orders, reconstructed
decisions or an executed replay. The long-option event side is LONG. Saved
realized P&L remains quote-proxy evidence, not a broker fill claim. Shared
allocation balances, unrealized P&L, Greeks, live contract chains, worker
health and broker positions are not inferred.

## Read-only and bounded

The reader requires the options application marker, uses SQLite `mode=ro` and
`query_only`, and reads one consistent transaction before explicitly closing
the connection. It validates bounded plan/event JSON, aware timestamps, finite
numbers, standard contract ownership and integer quantities. At most two
positions and 50 events are displayed. Unexpected oversized or invalid data
makes the whole snapshot unavailable, rather than implying a profitable or
flat account. Metadata and arbitrary event fields are not rendered.

Missing, wrong-type, invalid and busy ledgers show inventory unknown and no
invented balances. Reading preserves ledger entry activation and file bytes.
A futures ledger cannot be selected as an options ledger. The Options/Futures
market controls remain unavailable for activation because their desktop feed
and execution composition are not connected.

## Windows validation

```powershell
$TestRoot = Join-Path $env:TEMP ("atlas-options-view-" + [guid]::NewGuid().ToString("N"))
& .\.venv\Scripts\python.exe -m pytest `
    tests/asset_modules/test_options_ledger_view.py `
    tests/asset_modules/test_futures_ledger_view.py `
    tests/asset_modules/test_market_analysis.py `
    tests/asset_modules/test_desktop_markets.py `
    tests/asset_modules/test_strategy_views.py `
    -q --basetemp $TestRoot
if ($LASTEXITCODE -ne 0) { throw "Options view tests failed." }
```

These tests construct widgets with injected local data; they do not launch the
operator's Atlas runtime or connect market data. No live session is required.
