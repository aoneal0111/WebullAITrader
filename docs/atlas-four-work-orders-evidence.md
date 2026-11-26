# Atlas four-work-order offline evidence batch

This is a bounded research checkpoint, not a trading-policy release. It separates
observed PAPER evidence from simulated historical outcomes and never treats a
closed recording as an authoritative order ledger.

## How to reproduce on the stopped workstation

After generating the following **four existing read-only JSON reports** from the
matching current checkout, invoke the combiner. Do not substitute research fixtures
for the workstation campaign:

- `--ledger`: `app.strategy_research.paper_ledger_provenance` output from **explicit** candidate paths
- `--recording`: `app.strategy_research.closed_recording_evidence` output from an independently verified closed session
- `--entry`: `app.strategy_research.warrior_paper_entry_intervals` output from reconciled PAPER entry diagnoses
- `--micro`: `app.strategy_research.warrior_micro_cost_sensitivity` output based on source-reconciled hypothetical 1R barrier outcomes

All input paths must exist and be individually bounded. Choose a new output
filename; existing reports will not be overwritten.

```powershell
Set-Location "$env:USERPROFILE\WebullAITrader"
& ".\.venv\Scripts\python.exe" -m app.strategy_research.atlas_four_work_orders_evidence `
  --ledger "C:\path\to\ledger-provenance.json" `
  --recording "C:\path\to\closed-recording-review.json" `
  --entry "C:\path\to\warrior-entry-intervals.json" `
  --micro "C:\path\to\micro-cost-sensitivity.json" `
  --output "C:\path\to\new-four-work-orders-review.json"
```

## Work-order interpretation

1. **Ledger identity**: A valid PAPER SQLite schema confirms only that it is a
   PAPER-schema candidate. It does not confirm the database is the current
   runtime's authoritative store. A closed recording may supply event-type
   hints, never verified fills. Obtain a stable campaign identity and reconcile
   actual persisted fill IDs, order IDs, quantities and statuses before claiming
   completeness.
2. **Protection**: The synthetic stop-restart test exercises idempotence, but
   it cannot establish that workstation positions were protected. Verify
   live PAPER stop order quantity after partial exits, restart, replacement and
   cancellation; an accepted stop is not a confirmed stop execution.
3. **Early entries**: Exact source decision to authorization and authorization
   to order creation timings can be quantified when identity matches. Scanner
   first-seen and consumed gateway quote identities are missing from these
   historical forensic reports; do not interpret unrelated nearby bar triggers
   as scanner latency or imply a missed fill.
4. **Strategy selection**: Resolved 1R historical observations and assumed
   cost scenarios are only a retrospective screen. Report unresolved,
   ambiguous and censored counts prominently. No win-rate target may be claimed
   from this selected sample. Require chronological untouched walk-forward,
   position-level PAPER fills, costs, slippage and exit proof before promotion.

## Operating safeguards

Keep `WEBULL_TRADING_ENVIRONMENT=PAPER`,
`LIVE_TRADING_ENABLED=false`, and Atlas stopped during forensic inspection.
Do not change strategy thresholds, order paths, scanner universe, risk gates,
profit targets, stops, crypto settings or Grok toggles as a side effect of
these aggregate research reports. Missing evidence remains **UNVERIFIED**, not
zero, success or failure. Never delete or overwrite historical forensic
sessions. Any subsequent runtime change needs its own tests and CI review.
