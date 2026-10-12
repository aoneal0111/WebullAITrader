# Desktop native producer binding

This stage explicitly binds Warrior and Quick Scalper native entry callbacks to
`DesktopGrokProducer`. It does not construct a provider, enable Grok in desktop
configuration, launch Atlas, or enable LIVE orders. Default desktop operation
continues through the existing synchronous native paths.

## Binding and authorization

Construct a `NativeGrokReview` with the existing controlled PAPER service and
canonical execution bridge, then a `DesktopGrokProducer(review, permitted=...)`.
Call `sidecar.bind_grok_native_producer(producer)` while the sidecar is stopped.
The Scalper adapter and Warrior acknowledgement adopter must share that bridge.
Binding performs no model requests. The sidecar supplies its local execution
switch and installs both offer and refresh callbacks when the runtime starts.

Only final native entry branches offer signals. The initial lease inherits their
setup, session, halt and tradability checks. Signals must have current evaluation
evidence (at most two seconds old), and their original one-minute deadline must
remain unexpired. Preparation preserves original signal economics and metadata;
the controlled PAPER service rechecks quote, account and risk at dispatch.
The background review additionally rechecks the local execution switch before
submission, including when no new market frame has arrived.

Pending native evaluations refresh the same candidate rather than starting a
second model request. Warrior refresh requires matching lifecycle, session,
structural trigger and stop. Scalper refresh requires an executable assessment
with matching lifecycle, generation and structural stop. Changed structure or a
revoked local permission invalidates authorization. Refresh cannot extend the
original signal deadline. A missing refresh expires under the existing lease
freshness rules; it cannot keep authorization alive indefinitely.

## Completion and shutdown

Desktop completion polling applies review resolutions on the native owner lane.
The producer retains each request until native bookkeeping accepts its resolution.
Canonical acknowledgement and protective orders continue through the existing
handoff. Pending reviews neither count as orders nor release symbol ownership.

Stop revokes execution before draining. If a background review or reconciliation
is still pending, stop reports the existing Grok timeout and retains runtime
state for a later drain; it does not silently discard ownership. A closed review
cannot be restarted: bind a fresh producer after prior work has reconciled.

A shared Grok suite now clears advice for the engine absent from each immutable
batch. Empty reviews make no API call. This prevents a previously reviewed
Warrior candidate appearing in the master's next Scalper-only selection (or the
reverse). Existing API role cooldowns remain in effect.

## Verification

`tests/asset_modules/test_grok_desktop_producer.py` checks native callback binding,
current permissions after master review, changed Warrior and Scalper structure,
original deadlines, alternating-engine advice, owner-lane bookkeeping and stop
with blocked provider work. Tests use a fake provider and PAPER gateway; no paid
API calls or Atlas launches are required. Production provider construction and
configuration activation remain a subsequent integration stage.
