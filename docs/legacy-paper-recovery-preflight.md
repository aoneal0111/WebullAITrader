# Legacy PAPER recovery preflight

The active Atlas PAPER campaign is isolated from earlier legacy trades by
`DurablePaperExecutionStore.orders()`. The normal desktop gateway restores
active-campaign orders only. **Do not rewrite the old campaign IDs, copy
historical fills into the live order book, or manufacture replacement stops.**

## Offline, bounded observation

From a stopped Atlas workstation, run:

```powershell
Set-Location "$env:USERPROFILE\WebullAITrader"
& ".\.venv\Scripts\python.exe" -m app.paper_trading.legacy_recovery_preflight ".\data\sandbox\paper-execution.sqlite3"
```

The preflight opens an existing PAPER ledger via read-only SQLite URI, checks
identity metadata, loads at most 5,000 orders with payload size bounds, and
reports net filled quantities by **legacy symbol and lifecycle**. It lists
working saved stops/targets and flags observed stop gaps. It does not open a
trading session, invoke the broker, migrate campaigns, create/cancel orders,
repair stops, read credentials, or write to the database.

The results are **saved-order evidence only**. They cannot establish current
broker inventory, the working status of an old stop at the broker, whether a
legacy campaign should be reactivated, or whether the normal active campaign
will assume those positions. Nonzero legacy inventory is an operator review
condition, not permission to execute recovery.

Observed historical example (not a fixture or promotion criterion): BIAF 50,
ODD 197, SUNE 759, WYHG 30 legacy net shares; the stored SUNE stop covers
759, WYHG has a 30-share profit target rather than a stop, and BIAF/ODD have
no working legacy SELL protection in the inspected snapshot.

**Do not start the runtime or place recovery orders based only on this report.**
First verify current authoritative positions, account scope, entry ownership,
working protective orders, and a documented migration/reconciliation plan.


## Active campaign comparison (V2)

The same read-only snapshot also reports `active_campaign_inventory`:
net recorded BUY minus SELL filled shares per symbol, unfilled working BUY
and SELL quantities, and open-order counts. It separately reports
`active_nonzero_positions`, `active_working_orders`,
`active_negative_inventory`, and counts orders for any other campaign.

The active campaign is **never** netted against legacy inventory. A net-zero
active campaign does not imply that an excluded legacy position has disappeared,
that the account is flat at a broker, or that the old positions are protected.
Nonzero or working active inventory still requires a separate authoritative
runtime/account reconciliation before restart. This tool never creates or
amends orders or converts historical orders into current positions.
