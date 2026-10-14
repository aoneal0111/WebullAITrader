# Headless PAPER protection core — offline integration stage

`PaperProtectionHost` separates existing-inventory matching from a Qt window.
It starts no GUI, strategy, model, network client, thread or automatic service.
The caller must supply canonical market events. Only the synthetic subprocess
check is exposed as a CLI; there is no live feed or unattended launch command.

The host opens an existing dedicated Atlas PAPER equity ledger. A bounded
read-only preflight checks campaign/account identity, fills and active ownership.
Only Warrior and Quick Scalper lifecycles are supported. Unknown or conflicting
active ownership, short/fractional net inventory, invalid fills and missing
structural stops require reconciliation; the host does not invent replacements.
It reuses the desktop's order-authority position restoration, rather than folding
observational replay events as new fills. Quantities must agree with the orders.

Before recovery mutations it claims the same OS-held `atlas-paper-runtime.lock`
as the GUI. A second host or ordinary GUI process is refused while it owns that
lock. This is cooperative application ownership, not a security boundary against
arbitrary SQLite writers or legacy code bypassing the lock. Read-only inspection
remains available. A process exit releases the OS lock without deleting the file.

Recovery cancels residual BUY orders, retains existing inventory, and reconciles
structural stops through the canonical bridge. It preserves a correctly sized
working market liquidation rather than rebuilding profit targets over it.
Flat-position orders are cleaned up. Recovery can durably cancel entries or
create protection before a later recovery step fails; it is not an atomic ledger
rollback. Existing fills and campaign history are not rewritten or reset.

Matching starts paused for each symbol until a fresh supplied RESUME status.
HALT pauses it again; an older status cannot undo a newer one. A future adapter
must establish real tradability/status before publishing RESUME. The core itself
does not discover halts, entitlements, exchange sessions or corporate actions.
Status and quotes must have aware timestamps within five seconds of the supplied
clock. Future, stale, pre-start, duplicate and older quotes cannot consume
liquidity. Quotes at or before the last durable fill are also excluded after
restart. Prices and sizes are validated; no liquidity is synthesized. Existing
gateway temporal validity, reservations and matching rules still apply.

The core has no entry API. Quotes can fill existing stops, profit targets or
market exits, using actual supplied sizes. It does not choose new targets,
implement timed/momentum exit strategies, or flatten everything on startup.
No GUI or application configuration is changed by installing this component.

Run the separate-process mechanics check without opening the user's ledger:

```powershell
& .\.venv\Scripts\python.exe -m app.paper_trading.protection_host_check --synthetic
```

The check creates a temporary synthetic ledger with a partial entry and stop,
closes the original command runtime, starts a separate protection process,
verifies duplicate ownership rejection, supplies synthetic status/quote events,
and confirms the remaining position closes. It imports no Qt widgets and makes
zero network requests. Its result is not strategy-performance evidence.

Still required before unattended use: a verified live feed/status adapter,
subscription recovery, outage supervision, durable service lifecycle, and a
read-only GUI client that does not construct a second command owner. The GUI
currently refuses startup while the headless host owns its lock. This stage
therefore does not yet let the user close/reopen the GUI around a live service.
No execution continues after the host itself or its feed stops. No Windows
service is installed, and no greater-than-60-percent win rate is established.
