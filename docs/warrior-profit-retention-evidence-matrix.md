# Warrior small-profit retention comparison — evidence-only protocol

## Why

Captured October 9 NAUT PAPER data includes a 384-share entry near $2.40,
a sampled executable BID peak near $2.45, and a later filled stop near $2.29.
The sampled 0.45R gain is below Warrior's configured 0.75R first harvest and
1.50R profit-defense activation. This explains why those *thresholds* did
not activate; it does not prove that a lower setting produces more net profit.

## Command

From an **isolated checkout** using an installed Python 3.13 environment:

```powershell
& $Python -m app.trade_intelligence.knowledge.capture_profit_matrix $Capture > $Output
```

The input must contain the existing `orders` and `capture_records` ledger
fields. The command is offline and read-only: it does not invoke the Atlas
desktop, broker, controller, quote provider or gateway.

## Research grid

- Activation: 0.25R, 0.40R, 0.50R, 0.75R, 1.50R.
- Peak-profit remaining fraction: 50% and 75%.
- Quote eligibility: checked against observed timestamp, freshness, BID/ASK
  geometry and ledger fill boundaries.
- Optional additional per-share/per-side cost is a **declared sensitivity**;
  historical commissions already on the ledger are not overwritten.
- Every sampled threshold crossing is chronological. Missing observation
  intervals cannot establish an earlier crossing.

## Interpreting output

`armed` counts lifecycles with eligible sampled marks above the activation
threshold. `observed_signals` counts those with a later observed mark at or
below the declared peak-retention fraction. These are **signal counts**, not
orders, exits, winning trades, or realized returns.

`actual_closed_pnl` is always from the unchanged actual fill ledger.
`hypothetical_pnl` and `hypothetical_fill_price` are always null. An actual
profitable exit requires fresh executable quotes, an accepted outbound
order, durable ownership, and a confirmed fill; this tool cannot prove that.

Rows with incomplete ledger reconciliation remain explicit and do not count
as successful signals. The matrix does not output a recommended setting.

## Qualification before any runtime change

1. Run the grid against *all* available, reconciled Warrior lifecycles,
   including winners and losers. Do not optimize on NAUT alone.
2. Inspect all excluded and missing quote intervals. A sampled maximum is
   not necessarily the actual day's high.
3. Measure expected execution costs and fill feasibility separately using
   the PAPER gateway and replay fixtures.
4. Compare premature exits versus retained profitable continuations on
   unseen dates, with drawdown and net expectancy.
5. Add end-to-end gateway regression for partial harvest, persistent stop,
   partial fill, restart and denied amendment before promoting any new
   default.
6. Require forward PAPER evidence that each protective action is durably
   working or filled. Do not make Grok, entry approval, or scanner state
   an exit dependency.

**No profitability, win-rate or risk parameter change is authorized by this
offline research artifact alone.**
