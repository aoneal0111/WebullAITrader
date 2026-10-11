# Grok to controlled paper entry handoff

`GrokPaperEntries.review_and_submit` accepts an explicit batch of up to 32
immutable `PreparedEntry` records from a trusted strategy adapter. Each record
binds a candidate to its exact original Warrior or Scalper BUY LIMIT placement,
owning lifecycle, account, session and deadline. This stage supports original
whole-share equity entries only. It rejects replacements, cancellations, sells,
Crypto, Options and Futures.

Prepared entries require original owner, stop, risk and opportunity metadata.
The native placement factory and acknowledgement-to-bridge callback are described
in [Grok entry ownership](grok-entry-ownership.md). A successful acknowledgement
with an incomplete management handoff remains an accepted order, not a retry.

The caller supplies a current deterministic strategy authorization callback.
That callback must recheck setup confirmation, session, halt/tradability and
upstream strategy permission. It must return exactly `True`. Authorization runs
before model calls and again immediately before dispatch. AI receives candidate
evidence; it cannot change placement fields or create orders.

The owning engine advisers rank candidates, then the master ranks their selected
candidates. Only original objects bound to this batch can be dispatched. Prior
recommendations from another loop cannot supply order authority. Master advice
must match the suite session and be no more than two seconds old at dispatch;
candidate deadlines remain mandatory. Model errors do not bypass AI review.

Selected placements pass through `ControlledPaperTradingService`, which requotes
and independently applies account/risk, entry readiness, durable admission and
duplicate lifecycle checks. The controller supplies its existing durable command
identity and reconciliation. Ambiguous dispatches are not retried automatically.
Reports retain outcomes for entries already submitted if a later authorization
fails, so a partial batch is never reported as entirely unsent.

Call this adapter only from a dedicated strategy worker. A nonblocking lock
rejects concurrent batches; no growing backlog is created. Model calls hold no
paper-service lock. Existing stops and sells can continue while Grok is slow or
unavailable. The adapter starts no worker and construction makes no model call.

## Current integration boundary

This adds a callable entry dispatch adapter with real controller integration
tests. It does **not** intercept desktop strategy entries, subscribe to market
events or activate autonomous engine/master loops. A subsequent worker adapter
must supply confirmed placements and current strategy authorization, together
with position protection registration. Existing desktop behavior is unchanged.
No new configuration switch enables this handoff yet.

`GrokEntryWorker` now supplies an explicit bounded background runner for this
adapter. It refuses backlog and revokes subsequent entry checks on shutdown;
see [background worker semantics](grok-entry-worker.md). It still does not wire
desktop signals or register their protection automatically.

AI management changes remain unavailable until atomic order/position revision
checks and protection-preserving mutation adapters exist. Options/Futures also
still require contract data and execution adapters. This code makes no claim of
profitability or reduced model latency.

Validation uses mocked Grok responses and a durable paper gateway. Tests cover
both equity engines, master/engine abstention, revoked setup/quotes/risk/deadline,
duplicate lifecycle attempts, foreign batch recommendations, model failures,
partial results and protective exits during a blocked model call.
