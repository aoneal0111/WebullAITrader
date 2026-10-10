# Desktop PAPER controller activation

`ATLAS_PAPER_CONTROLLER_ENABLED=true` connects Warrior and Quick Scalper's
existing PAPER bridge and desktop order service to one admission controller.
The flag defaults to false. It does not enable either strategy, change their
23-strategy execution mappings, enable LIVE execution or start a worker.

The controller is constructed after authoritative order/position restoration
and existing protection reconciliation. It uses a separate SQLite ledger under
`<execution database directory>/paper-controller/<campaign hash>.sqlite3`.
Existing execution databases and capture history remain intact. Allocation is
bound to the durable campaign's starting capital: capital is capped by the
smaller of starting cash and the configured gross exposure fraction of starting
equity; planned open risk is capped by the configured campaign loss fraction of
starting equity. These are shared entry reservations, not spendable broker cash
or a guaranteed loss ceiling. Current buying power and risk approval still come
from Atlas's authoritative account context.

New BUY entries retain upstream setup, halt/tradability and session permission,
then check a fresh executable bid/ask through the existing Webull quote source.
The callback checks bridge readiness, provider bid/ask timestamps (two seconds),
stop/limit geometry, symbol authorization mode and current deterministic risk
sizing. Account risk is checked again between reservation and dispatch. Actual
submission still uses the existing TradingService and its session/policy gates.
No model or new market data feed is involved. The additional quote request runs
only for actionable entry attempts, not each market tick; it can add REST latency
and refusals when the provider is stale or unavailable.

Original strategy, opportunity, catalyst/economics and replacement metadata are
preserved. The controller adds command identity, metadata policy fingerprint,
absolute deadline and a stable client-order ID. Broker rejection/exception
without stored order evidence keeps the reservation uncertain; recovery never
resubmits it. Campaign rotation invalidates the running adapter and requires a
restart to bind the new campaign. Persistent allocation changes require an
explicit migration; changing settings cannot silently reset active budgets.

Existing unmanaged working orders or nonzero filled inventory block new
controller entries. They are not cancelled or sold by installation. Existing
management must close them normally before new entries become eligible. This
version waits for flat legacy exposure rather than guessing ownership or
adopting it into a new empty ledger. Reconciliation uses the authoritative current
campaign order book, not historical research or GUI P/L.

SELL exits, protective stops and cancellations bypass entry admission and its
quote request. They retain all existing quantity/lifecycle/protection checks.
An unavailable controller or AI cannot suppress these commands. Unfilled entry
replacements can reserve again after their predecessor is terminal and flat.
Filled/partial predecessors, add-ons and manual new BUY orders are refused in
controller mode until reservation amendments and operator ownership are
supported. The disabled mode preserves existing behavior.

Status is available as `DesktopComposition.paper_controller_service.status()`
and the `paper_controller` group in read-only composition validation snapshots.
Refused requests report `PAPER_CONTROLLER: <reason>`; the entry funnel also
records controller rejection attribution. Worker separation and AI supervision
remain subsequent stages. These checks verify coordination and protection;
they do not establish strategy profitability.

## Windows activation on the next normal launch

Run the focused tests first, then in the PowerShell used to launch Atlas:

```powershell
$env:WEBULL_TRADING_ENVIRONMENT = "PAPER"
$env:LIVE_TRADING_ENABLED = "false"
$env:ATLAS_PAPER_CONTROLLER_ENABLED = "true"
& .\.venv\Scripts\python.exe -m app.gui.app
```

Environment changes do not affect an already running process. Restart through
normal Atlas controls and preserve position protection; do not kill a process
with open positions. To return to the original entry path on a later normal
launch, set `ATLAS_PAPER_CONTROLLER_ENABLED=false`. No ledger is deleted.
