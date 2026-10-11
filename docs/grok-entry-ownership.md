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

## Remaining desktop integration

This is an explicit ownership handoff, not automatic desktop activation or a
complete position-management adapter. A runtime callback must adopt the native
acknowledgement, install the owning strategy's management state, reconcile any
synchronous fill, and retain subsequent fill/quote observers. Adoption alone
does not install Warrior's forward-runtime state or create a stop. The tests
explicitly call the existing deterministic stop path after filling the entry;
they verify ownership and that protection does not depend on another AI call.

Desktop construction and strategy submission remain unchanged. Fresh signal
preparation, dispatch-time setup authorization, completion polling, management
state installation and shutdown reconciliation must be connected before enabling
autonomous Grok entries. No live broker route is added.
