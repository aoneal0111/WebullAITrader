# Read-only Atlas PAPER equity viewer

`python -m app.gui.paper_viewer --ledger PATH` opens an optional saved-ledger
viewer using Atlas styling, its existing order widgets, and separate Warrior
and Quick Scalper views. The normal Atlas desktop and its market tabs remain
unchanged. This is an equity viewer, not a replacement workstation or a read-only
mode for the full main window. Crypto/options/futures ledgers are not loaded.

The viewer creates no desktop composition, trading service, command factory,
broker client, strategy worker, learning collector or model connection. It
does not acquire, touch, or release `atlas-paper-runtime.lock`. It may inspect
the ledger while a cooperating protection host or normal desktop owns that
lock. Closing the viewer closes only its own window. A normal Atlas trading
GUI still refuses to start while the headless host owns the shared lock.

The reader opens an existing source through SQLite `mode=ro` and `query_only`,
inside one read transaction. It checks the dedicated PAPER account, schema and
active campaign; validates saved order/fill quantities, timestamps, prices and
ownership consistency; and constructs immutable order read models. It never
opens the durable store's writer/recovery class or submits repair commands.
Basic envelopes are checked before campaign filtering so damaged records cannot
silently disappear into the legacy-campaign fallback.
SQLite may use existing WAL/shared-memory facilities for a concurrent reader;
read-only SQL does not promise the absence of all SQLite sidecar activity.

Reads are bounded to 100 metadata entries, 5,000 current-campaign orders,
32 MB total payload and 128 KiB per order. A 50 ms busy timeout and nominal
750 ms progress/row-processing budget bound normal reads; this is not a hard
real-time I/O deadline. Oversized, malformed or incomplete results are rejected
as unknown, rather than truncating inventory. A busy or failed read returns no
authoritative counts, clears displayed orders and net fills, and labels the
data unavailable. Refresh manually to retry. There is no automatic polling.

The equity table uses Atlas's existing working/recent formatter: all saved
working orders plus up to 25 recent terminal orders. Strategy order panels use
their existing recent-order limits and retain all working orders for the
selected lifecycle owner. Strategy net fills are calculated from the complete
bounded snapshot, not just the visible recent rows. Unknown lifecycle ownership
remains visible in the equity order view; it is never guessed into a strategy.

Saved orders and net fills do not establish broker inventory, account cash/P&L,
runtime liveness, feed freshness, halt status or protection effectiveness.
The read time is local inspection time, not a provider heartbeat. The viewer
therefore always reports runtime/feed health UNKNOWN. No service is started by
opening it, and no headless feed is activated.

Read-only inspection without Qt or a window:

```powershell
& .\.venv\Scripts\python.exe -m app.gui.paper_viewer `
    --ledger data/sandbox/paper-execution.sqlite3 --check
```

Open the optional viewer manually in another PowerShell:

```powershell
& .\.venv\Scripts\python.exe -m app.gui.paper_viewer `
    --ledger data/sandbox/paper-execution.sqlite3
```

`--check` returns zero only for an AVAILABLE validated saved snapshot. Missing,
busy or invalid sources return nonzero without creating a database. It neither
needs Webull credentials nor loads application configuration. Tests construct
temporary ledgers and verify reads during canonical host ownership, source
preservation, invalid/busy handling, refresh after fills, Qt-free CLI inspection
and reuse of existing strategy views. They do not establish strategy returns.
