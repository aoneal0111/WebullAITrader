# Grok desktop completion ownership

`GrokRuntimeHandoff` binds a bounded background review to original immutable
`RuntimeEntry` context. It requires the canonical native bridge and the same
controlled PAPER service bound to that bridge. A Warrior entry includes its `PreparedEntry`, original
`MomentumEntrySignal`, float provenance and symbol authorization. Symbol,
lifecycle, entry and stop must match. Scalper uses the native order metadata.
The existing worker/controller still require current setup authorization,
account/session, quote, risk, ownership and deadline checks. The `authorize`
callback must consult current deterministic evidence; do not replace it with
an unconditional approval in production.

`WarriorDesktopSidecar.bind_grok_runtime_handoff(handoff)` explicitly binds the
completion coordinator. `submit_grok_entries(entries, engine_evidence=...,
account_evidence=...)` offers already prepared native placements without waiting
for a model. Disabled or unstarted sidecars refuse offers. There is one batch,
including unread completion and any acknowledgement awaiting adoption. Busy
work is refused without a queue or automatic buy retry.

The background acknowledgement callback registers canonical bridge ownership
and reconciles native protection. Fill callbacks keep canonical stops available
before completion polling installs Warrior's management state. Protection uses
its own nonblocking recursion guard; it does not acquire the completion lock
or wait for the provider. Runtime adoption happens on the sidecar's serialized
processing lane before the next market evaluation, or through the explicit
`poll_grok_completions()` owner-lane pump. It never runs in the model callback.

A successful broker result is retained even when ownership handoff was reported
incomplete. Failed runtime adoption is retried as management adoption only.
Terminal, canonically verified, flat lifecycles release the handoff without
reviving planned inventory. An uncertain dispatch, gateway failure or unbound
completion keeps further reviews blocked. `recover_acknowledgement(candidate_id,
result)` accepts a result found through durable reconciliation only after the
bridge verifies it against the canonical order book. Absence of an order is not
proof of no submission. Unbound/duplicate completion corruption stays latched
for explicit investigation.

Shutdown revokes entry review permission before waiting for the processing
lane. If review is still active or an acknowledgement is unresolved, sidecar
shutdown raises rather than discarding its service/writer. A later stop attempt
drains late acknowledgements even though new-entry permission is off. A stopped
handoff can be replaced only after its worker is quiescent and all retained
entries are reconciled. `grok_entry_status()` exposes bounded completion status.
Existing broker protection and durable restart reconciliation remain required;
an AI worker stop cannot retract a submission already entering the service.

## Activation boundary

Normal desktop composition still uses native synchronous Warrior/Scalper entry
submission. This change supplies explicit offers and automatic completion drain
when a handoff is bound; it does not automatically prepare or intercept native
entry signals, configure Grok credentials, launch Atlas, or activate autonomous
Grok trading. The next producer integration must prepare native placements,
maintain current setup authorization and classify pending reviews separately
from rejected/filled entries. Tests use mocked Grok responses with the real
controlled PAPER gateway. There is no live broker route.
