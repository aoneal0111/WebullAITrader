# Grok entry acknowledgement and ownership

`PreparedEntry.from_placement(placement, evidence=...)` creates an advisory
candidate from a native equity placement without changing the placement.
Its candidate ID binds the account, session, lifecycle and original client ID.
Canonical quantity, limit, structural stop, risk and opportunity override any
similarly named extra evidence. All prepared entries now require matching
`strategy_owner`, a positive risk budget, an opportunity and a finite structural
stop below the limit. Missing ownership cannot silently enter the AI path.

`GrokPaperEntries(..., on_acknowledged=callback)` invokes the callback immediately
after a successful controlled submission, before reporting completion. It does
not invoke the callback for abstention or refusal. The callback runs on the
background review thread and must not wait for AI or touch Qt widgets.

`AutonomousPaperExecutionBridge.adopt_entry_acknowledgement(placement, result)`
registers the acknowledgement in the existing bridge. It checks the controlled
account/session, controller-derived client ID, native order-book entry, lifecycle,
owner, quantity, prices, metadata and structural stop. It refuses an unrelated
acknowledgement or a conflicting symbol owner. It never submits or retries an
entry. Repeated adoption is idempotent. A terminal unfilled order is remembered
without reviving active inventory. Adoption still works after new entries are
disabled because an already acknowledged order requires ownership.

The controller replaces the original client ID with its deterministic command
ID. Adoption verifies that routed ID against both the result and the canonical
book; comparing only with the original placement ID would reject valid orders.

If a callback raises or returns anything other than `True`, the outcome is
`ACKNOWLEDGED_HANDOFF_INCOMPLETE`. The successful result, including broker order
ID, remains attached. Treat this as existing or uncertain exposure needing
reconciliation, never as permission to retry the entry.

## Native runtime management handoff

Warrior's `adopt_acknowledged_entry(signal, placement, result,
float_provenance=..., symbol_authorization=...)` installs the same management
state used after a native synchronous submission. Call it on the serialized
owning runtime thread after polling a background completion, not directly from
`on_acknowledged`. It verifies the original signal, canonical order and bridge
ownership before installing state. Desktop construction supplies the canonical
order source and bridge adopter; ordinary entry submission remains unchanged.

Scalper's `adopt_acknowledged_entry(placement, result)` uses its existing locked
ledger reconciliation to restore ownership, position state, stop and adaptive
target. Prepared scalper entries require a finite adaptive target above the
entry limit before review begins.

Both runtime methods use bridge adoption with `require_active=True`. A terminal
unfilled or fully closed lifecycle cannot revive planned inventory. Repeated
active adoption is idempotent and neither method submits another buy. Synchronous
partial fills are reconciled to actual inventory, and later fills use the
existing protection reconciler. A protection submission failure retains state
for reconciliation rather than treating the acknowledgement as a failed entry.
A `True` handoff means state adopted, not proof that every protective leg is
active; continue the native fill and quote observers and inspect protection
status separately.

## Remaining desktop integration

These are explicit management APIs, not automatic Grok activation. Fresh signal
preparation, dispatch-time setup authorization, owner-thread completion polling
and shutdown reconciliation still need to be connected before enabling
autonomous Grok entries. Bridge adoption alone still does not install Warrior's
forward-runtime state. No live broker route is added. Validation uses mocked
Grok responses and the real controlled paper gateway, including unfilled,
partial and complete entries, mismatched acknowledgements and protection retry.
