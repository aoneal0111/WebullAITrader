# Atlas execution and learning audit — 2026-09-22

Scope: source and deterministic PAPER tests, based on commit 605eb3b.
No connection to the user's Windows runtime or authenticated market subscriptions.
This patch does not establish profitability, authorize live trading, or promise a maximum realized loss.

## Findings and implemented repairs

- Partial entry fills repeatedly replaced stops. The desktop PAPER bridge now amends the existing protective stop under the gateway lock, preserving fills and refusing wider stops or oversized reservations.
- Cancellation read an order before taking the gateway lock. It now rereads inside the lock so an intervening fill cannot be discarded.
- A split target/stop bracket reserved target shares away from downside protection. When its stop triggers, the gateway atomically cancels correlated pending orders and assigns the full remaining lifecycle inventory to that stop before matching. The durable batch preserves this transition over restart. Triggered stops remain triggered after a partial fill even if price rebounds.
- Quote reconciliation consumed the quantity decrease needed by bar-level target management. It now preserves that evidence. FIRST_TARGET and SECOND_TARGET also consult durable cumulative fills and their original quantity budgets, preventing the same milestone from repeatedly selling half the remainder after recovery.
- Desktop account context always reported risk approval. New entry approval now requires complete valuations, campaign equity above its loss threshold and exposure below its cap. Initial sizing rejects excessive structural stop distance; adaptive repricing rechecks that distance.
- Outcome evaluation included returns from rejected opportunities. Economic metrics now include only selected predictions in chronological order. Unselected challengers cannot access holdout examples through champion comparison.
- Later decision snapshots reused outcome labels anchored to the original snapshot. Those later snapshots are now unlabeled until a correctly anchored outcome exists.
- New offline cost sensitivity reports selected hypothetical opportunity returns under predeclared costs of 0, 0.10, 0.25 and 0.50 R. They explicitly carry execution_validated=false and promotion_authorized=false. They are research, not broker-fill performance.

## Provisional risk defaults

| Control | Default | Meaning |
|---|---:|---|
| Per-trade equity risk | 0.5% | Existing planned risk budget, bounded by existing $100 cap |
| Maximum initial structural stop distance | 8% | Reject overly distant stops; do not move the structural stop closer to force entry |
| Campaign loss entry gate | 2% of starting campaign equity | Blocks fresh approvals; persists through restart via account state |
| Gross exposure entry gate | 50% of current equity | Blocks fresh approvals at/above threshold |

These values are provisional engineering guardrails, not optimized trading parameters. Campaign loss is measured from starting equity, not intraday high-water equity. The entry gate does not liquidate positions or cancel all previously working entries. Open orders, gaps, slippage and partial fills can exceed planned risk. The gross gate is an admission check, not a complete reservation-based portfolio allocator.

## Trading and learning architecture

The legacy app/learning trainer targets whether an action was BUY. Its scores must not be interpreted as calibrated probabilities of profit. The outcome-based app/opportunity_learning package is the appropriate offline research path, but it does not yet prove an executable edge.

The next engine should use the following evidence chain:

1. Record point-in-time setup, session, catalyst publication/receipt times, quote age, bid/ask and available depth. Unknown or unavailable inputs remain unknown.
2. Compare a frozen baseline against one candidate at a time. Group evaluation by session and symbol to avoid overlapping examples and leakage. Keep final holdout unopened until candidate selection is complete.
3. Use actual net execution outcomes, including spread, slippage, fees, rejected orders, missed fills and partial-fill paths. Hypothetical bar targets and action-imitation accuracy are insufficient.
4. Require independently positive net expectancy with adequate uncertainty analysis, bounded drawdown and stability across sessions. A positive mean or short winning streak is insufficient. The new iid interval is descriptive only; session-clustered inference remains necessary.
5. Run the selected candidate in shadow and then PAPER under fixed risk budgets. Promote only after independent execution validation. Do not let the learner raise risk or enable live trading.

Implemented in this patch: corrected point-in-time label use, selected-trade economics, holdout separation and cost-stress reporting. Not implemented or claimed: a validated profitable challenger, automatic strategy promotion, or a wealth-generation system. The user's execution journal and enough independent sessions are required to test that claim honestly.

## Market-data wiring and remaining execution limitations

Desktop composition passes the shared REST market-data client to the order-flow sidecar. Sidecar code can forward quote depth, sizes, timestamps and cached flow context. Source wiring does not prove the user's L2/NBBO entitlement, freshness or active receipt. Subscription and runtime diagnostics remain required. Cached footprint/capital-flow context must not be described as instantaneous order-book evidence.

The new downside switch is local PAPER logic requiring the runtime and fresh quotes. It is not a broker-hosted OCO guarantee. It covers brackets with a working correlated stop; a full-position passive target with no remaining stop is not covered by that mechanism and needs a separate lifecycle design before broader execution claims. Existing paper depth matching can reuse displayed liquidity across observations and is not a calibrated queue/fill simulator.

The screenshot alone cannot establish why the desktop stopped. Market-data recovery, runtime exceptions and order handling must be distinguished using the Windows stdout/error logs. Historical cancelled orders themselves were not shown to double-count active inventory: durable order/event reads are campaign-scoped.

## Validation

Focused coverage includes partial target reversal, partial stop rebound after durable restart, target idempotency, incremental entry-stop amendments, concurrent fill/cancel, persistence failure without in-memory bracket mutation, risk admission and outcome economics.

The wider regression run has one pre-existing desktop historical-journal test failure (journal is None); the identical test fails at unchanged base 605eb3b. Four pre-existing forward-capture tests also fail on both base and patch: historical experiment control and three old exit/profit-defense expectations. These are not asserted fixed or hidden as passes. Windows runtime acceptance is outstanding.

## Research basis

FINRA Regulatory Notice 16-19 explains why a triggered stop can execute away from its stop price and why stop-limit protection has execution tradeoffs:
https://www.finra.org/rules-guidance/notices/16-19

Bailey, Borwein, Lopez de Prado and Zhu, The Probability of Backtest Overfitting, motivates avoiding repeated strategy selection on the same history:
https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf

These sources motivate execution realism and independent evaluation. Neither identifies a universally best or guaranteed profitable trading engine.
