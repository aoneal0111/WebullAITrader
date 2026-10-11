# Background Grok paper entry worker

`GrokEntryWorker` runs an explicit `GrokPaperEntries` batch on a background
thread. Construction starts neither a thread nor a provider request. The
trusted caller submits immutable `PreparedEntry` objects; evidence is copied
as JSON before submission returns. A batch contains at most 32 entries and
combined engine/account evidence is limited to 64 KiB. The dispatcher retains
all original account, session, deadline, setup and paper-controller checks.

There is one in-flight batch, including its unread completion. Additional
submissions return `BUSY` and are not queued or retried. `poll()` returns a
completion without waiting for AI. Consume the result before submitting another
batch. Entry acknowledgements remain in the durable controller and order book.

`close()` prevents future batches and revokes subsequent dispatch checks in an
active review. The dispatcher checks this permission before engine reviews,
before master review, before final authorization and again after authorization.
Provider requests are not forcibly interrupted. `close(timeout=0)` requests stop
without joining; optional waits are limited to one second. A `False` result means
the thread is still stopping, not that shutdown finished. The final report stays
available through `poll()`. A stopped worker cannot be restarted.

Stop is not an atomic broker cancellation barrier: an order already entering
the service can still be acknowledged, and a stop racing immediately after the
last check cannot retract it. Never discard the durable acknowledgement or
remove protection because the worker stopped. The existing entry deadline and
controller reconciliation remain authoritative. Runtime shutdown must retain
ownership and protection for every submitted or uncertain entry.

The worker holds no queue lock or paper-service lock while waiting for the model.
The owning strategy's protection observer and deterministic exits must continue
independently. A test blocks the model, fills an existing protective sell through
the real paper gateway, requests shutdown and confirms the new candidate was
never submitted.

## Integration boundary

This is an explicit worker API, not desktop activation. It does not move the
existing Warrior/Scalper strategies into observation process workers, subscribe
to market events, construct new signals, register position managers or enable
autonomous Grok trading. Desktop code is unchanged. Before activation, an engine
adapter must bind fresh deterministic signals, strategy-owner metadata and
position protection/reconciliation to the immutable placements, and provide
current authorization. A caller must also poll results and handle STOPPING and
uncertain dispatch explicitly. No live broker route is added.

Validation uses mocked Grok responses with the durable paper controller. It
covers both engine ownership routes, successful entry, backpressure, evidence
mutation, bounded invalid inputs, provider failure, shutdown during engine and
master review, shutdown during final authorization, and independent protection.
