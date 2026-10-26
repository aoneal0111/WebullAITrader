# Manual bounded fresh equity context capture

Preparation does not launch Atlas. `tools/prepare-observer-session.ps1` creates
a private Desktop directory and selects `preparation-summary.json`. It refuses
preparation while an Atlas GUI/bootstrap process is already running. Nothing is
merged into the installed checkout, no installed source or settings are edited,
and no credentials are entered or printed.

The source snapshot is copied from the installed repository, including tracked
or untracked local app-source edits. It is not a checkout of the research
branch's trading implementation. A fixed allowlisted patch adds only the four
native observer-hook interfaces, using exact unique context matches or exact
already-applied matches. Any ambiguous/missing context fails preparation.
Only five observer modules are overlaid. Symlinks/junctions and excessive source
counts/bytes are refused; bytecode and `.env` are not copied. The private source
snapshot must remain local.

Preparation compares the original and staged effective configurations in clean
isolated Python subprocesses, both reading the installed `.env` explicitly.
Secrets are redacted before configuration hashing. Source-relative default-path
changes fail comparison rather than redirecting runtime data silently. It then
checks interfaces/imports and synthetic observer transport without a GUI.
Networking is blocked in these check subprocesses. Source, staged-source and
configuration fingerprints are checked again before any manual start. A changed
baseline requires re-preparation; no changes are reverted or overwritten.

`PREPARED_NOT_STARTED` means only preparation succeeded. The generated
`Start-Observer-Capture.ps1` is a separate manual action for a regular session.
It refuses outside the installed exchange calendar's CORE session and near the
close. It does not auto-start equity scanning/trading; the operator uses the
normal Atlas controls. It retains normal runtime ownership/duplicate-instance
handling and uses the normal installed data paths. Codex does not execute this
start script or invoke the GUI.

The manual child loads the copied baseline with passive hooks, binds capture
to the effective Warrior and Scalper policy configurations and never enables
Grok order review or live trading. In addition to requiring PAPER/live-off and
Grok-review-off flags, a process-local request blocker refuses every native
`GrokClient.request` before transport, including crypto's model path if keys
already exist. It remains in force until that manually started Atlas exits.
No model fallback, key deletion, `.env` change or subscription is involved.
Protective exits remain on their normal paths. Normal PAPER strategy behavior
is retained; observer order counts must not be read as counts of all ordinary
PAPER activity. To use the normal launcher later, close this process normally.

The observer attaches during desktop construction but waits for the existing
first new scanner evaluation diagnostic after attachment. The background
runtime service's RUNNING flag is explicitly insufficient: it was observed
before manual scanner START in the second empty 2026-10-26 capture.
The existing diagnostic is sampled every half second; the observer never
starts the runtime or requests quotes.
A separate monotonic 15-minute startup timeout bounds an unattended GUI.
Waiting and capture are both capped at the remaining regular-session time.
Once a new scanner evaluation is observed, the five-minute observation budget starts; a later
STOP/START cannot extend or restart it. Pending startup accepts no records.

Capture accepts at most 100 records, the existing byte budget, or five active
minutes, whichever limit occurs first. Producers check the monotonic deadline
before enqueueing; the monitor closes only observer threads/files. Scheduling
or writer draining can delay the final summary beyond the submission deadline.
Atlas is not stopped, positions are not closed and working orders are not
cancelled by capture shutdown. Missing bars, absent native quote paths, disabled
engines, quarantine, backpressure or stale input can still yield zero records.
No quotes are requested or signals fabricated to fill the capture.

The 2026-10-26 empty capture exposed the old timer defect: it ended at
15:42:06 UTC, before runtime startup at 15:42:17 UTC. Regression tests now
simulate six idle GUI minutes followed by a full five-minute capture, bounded
startup without START, scanner activity at monotonic zero, and market close
while waiting or collecting. These tests do not launch the real GUI.

`session-summary.json` contains owner counts, dropped/refused outcomes, quote
ages at native assessment and observer receipt, queue-delay ranges, monotonic
duration/wall-clock comparison and before/after source/policy checks. It also
separates `startup_wait_seconds`, `observation_started_at` and
`observation_elapsed_seconds`. `evidence_status` distinguishes
`NO_ENGINE_RECORDS`, `PARTIAL_ENGINE_RECORDS` and `BOTH_ENGINES_RECORDED`.
`TIME_LIMIT` means the observation window elapsed; `STARTUP_TIMEOUT` means the
scanner never produced a new evaluation; `SESSION_END` caps either phase. None of these
status values alone validates input provenance or a strategy. Future
or stale quote measurements remain visible. Timing ranges are diagnostic, not
proof of a particular provider/clock defect or hard real-time latency. The
summary deliberately requires review and never sets online observer readiness.
Private context and copied source remain in the output directory; share only
the summary. No private PnL or account/fill data is published.

Saturday preparation is useful; starting a fresh equity capture must wait for
the regular-session gate. Offline fixtures and a source-copy compatibility pass
do not establish real capture throughput, quote freshness, strategy performance
or online Grok behavior. No automated or scheduled GUI launch is created.


The second empty summary began observation at 16:49:28 UTC, while its associated
log records scanner runtime startup at 16:54:12 UTC. Its three queued items and
absent quote timing are consistent with shared bar updates, not three captured
engine assessments. The corrected marker is `SCANNER_FIRST_EVALUATION` in both
preparation and session summaries. An unchanged marker from an earlier run
cannot start this capture. Tests exercise the actual PerformanceDiagnostics
marker, including runtime startup without any scanner evaluation.

Tap summaries now separate `bars_queued` from `assessments_queued`, with
per-engine assessment counts. These measure calls to the existing observer
hooks, not all possible engine decisions. Current context hooks remain on
execution-quote confirmation paths: disabled strategies, qualification gates
and missing execution quotes can yield zero assessable records even after
timing is repaired. The observer never requests data or bypasses gates to
manufacture records. A zero-record result is not a strategy performance test.

## Quote timing and early Scalper diagnostics

Warrior's native event timestamp may precede a synchronous REST quote request.
The execution path already evaluates provider ages against `confirmed_at`.
The observer now uses that same confirmation time for the recorded quote
assessment and retains the earlier native-event age as a separate diagnostic.
A quote arriving after the native event is not by itself a future-dated quote
at confirmation. Provider times remain unchanged; genuinely future or stale
quotes still fail the original two-second capture contract.

`observer_queue_delay_seconds` now measures enqueue-to-worker time. Previously
its interval began at the native event and could include the REST request and
other native processing. `native_event_to_confirmation_seconds` and
`confirmation_to_capture_seconds` expose those other intervals separately.
No request timestamps are inferred from the event; request duration itself is
not measured by these intervals. Existing short bar-history availability checks
still use the original native event cutoff, so later bars are not admitted.

`entry_diagnostics` exports only cumulative numeric Quick Scalper counters and
equity funnel stage counts, with deltas since observer attachment. Counter
reset detection invalidates the delta rather than presenting negative counts.
Maximum-age gauges are not subtracted as though they were event counts. No
symbols, setup records, fills, account values, credentials or PnL are included.
`scalper_readiness` states configured enabled, runtime enabled and sidecar
execution-permitted values, without modifying them. Unknown fixture attributes
remain null. These are observations at summary close, not activation authority.

In the current source, `QUICK_SCALPER_ENABLED` defaults false. Enabled Scalper
stream assessment is independent of Warrior breakouts; a missing observer
confirmation record does not establish why that path stopped. Compare stream
observations, assessments, opportunities, initial-policy waits, authorization,
quote availability, reconfirmation, risk and bridge counters before changing
strategy behavior. An existing `%TEMP%/atlas-performance/*.json` checkpoint
already exposes these counters and can be read while Atlas runs. Its capture
and runtime timestamps must match the run under investigation. Inspect only
numeric counters rather than sharing the private artifact wholesale.

The empty equity release registry still denies new BUYs from either engine.
A diagnostic repair or a public scalping example does not qualify a policy.
No settings, live execution, release approvals, provider requests or protective
exit behavior changed in this patch. The provider's old quote timestamps were
not repaired or replaced with receipt time; fresh native input still requires
provider evidence. Tests cover post-request timing, real future timestamps,
45-second-old quotes, counter deltas/reset and disabled configuration reporting.
