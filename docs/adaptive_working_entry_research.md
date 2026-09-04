# Adaptive Working Entry Reassessment (research phase)

This sidecar observes active autonomous Warrior PAPER `BUY LIMIT` entries on
existing quote and trade events. It detects material changes, creates a fresh
point-in-time risk geometry, and appends a shadow recommendation. It is
disabled by default and is ineligible in LIVE.

The event flow is one way:

`market event -> PAPER gateway -> Warrior observation -> adaptive observer -> bounded worker -> JSONL`

The adaptive package receives read callbacks for current orders, positions,
and Warrior state. It has no placement, cancellation, replacement, broker,
risk-authorization, or execution port. Every input, recommendation, and future
label carries `research_only=true`, `execution_authorized=false`, and
`production_promoted=false`.

## Material changes

Admission is event-driven and semantic. Price/quote displacement is measured
in original R; changes to spread, fresh reference/stop, setup state, technical
actionability, acceleration evidence, and the near-expiry edge are also
explainable reasons. Identical semantic updates are suppressed. Per-order
snapshots and signatures are LRU-bounded, and worker admission is nonblocking.
PAPER owns global and per-symbol active-order indexes, so lookup is proportional
to active orders for the event symbol rather than retained terminal history.
The worker uses nonwaiting lock admission plus `queue.put_nowait`; contention or
capacity pressure is counted and dropped. Evaluation and JSONL I/O stay on the
worker thread.

## Point-in-time semantics

`market_event_at` is the timestamp of the quote/trade evidence,
`order_submitted_at` is the authoritative order creation time, and
`order_state_at` is the timestamp of the immutable order snapshot. Warrior and
position evidence carry separate provenance timestamps. `observed_at` is when
the observer finishes adopting these inputs, and `decision_cutoff` is that
causal boundary. Contract validation requires every provenance timestamp to be
at or before the cutoff and specifically forbids order creation after it.
Future-dated Warrior or position evidence is rejected rather than moving the
cutoff forward.

## Recommendations and risk

Price moving upward is not sufficient to reprice. A reprice candidate requires
an explicit fresh reference and stop, actionable current structure, adequate
setup quality, acceptable spread, bounded price drift, acceptable risk
inflation, and a recalculated quantity. Missing gates produce KEEP, WAIT,
ABANDON, or INSUFFICIENT_EVIDENCE as appropriate.

The research sizing provenance is the original plan budget:

`original quantity * original risk/share`

Filled/current exposure consumes its proportional share. Remaining budget is
divided by fresh risk/share with floor rounding and capped at remaining order
quantity. The original quantity is never copied into a repricing candidate.
This does not invoke or alter production sizing.

## Outcomes

Future outcome labels are separate objects and APIs. Evaluator inputs reject
future timestamps. The bounded outcome tracker supports 5, 15, 30, 60, and
300-second horizons using an explicitly hypothetical observed-price-touch fill model; it
never reports a modeled touch as an actual fill. Recommendations append to the
configured JSONL path, while labels append to the sibling `outcomes.jsonl`
artifact only after their recommendation has been completed.

Configuration:

- `ADAPTIVE_ENTRY_RESEARCH_ENABLED=false`
- `ADAPTIVE_ENTRY_RESEARCH_PATH=data/adaptive_entry_research/recommendations.jsonl`
- `ADAPTIVE_ENTRY_RESEARCH_QUEUE_CAPACITY=512`

The existing 60-second `ENTRY_STALE` policy is independent and unchanged.
