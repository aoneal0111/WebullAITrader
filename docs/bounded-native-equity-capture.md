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

Capture starts when desktop composition is constructed. It accepts records for
at most five minutes, 100 records, the existing byte budget, or the remaining
regular session, whichever limit occurs first. Producers check the monotonic
deadline before enqueueing; a short monitor interval closes only the observer
threads/files. Atlas is not stopped, positions are not closed and working orders
are not cancelled by capture shutdown. Missing bars, absent native quote paths,
disabled engines, quarantine, backpressure or stale input can yield zero records
or incomplete owner coverage. No quotes are requested or signals fabricated to
fill the capture. Click normal START promptly if appropriate; the five-minute
window does not wait indefinitely for user activity.

`session-summary.json` contains owner counts, dropped/refused outcomes, quote
ages at native assessment and observer receipt, queue-delay ranges, monotonic
duration/wall-clock comparison and before/after source/policy checks. Future
or stale quote measurements remain visible. Timing ranges are diagnostic, not
proof of a particular provider/clock defect or hard real-time latency. The
summary deliberately requires review and never sets online observer readiness.
Private context and copied source remain in the output directory; share only
the summary. No private PnL or account/fill data is published.

Saturday preparation is useful; starting a fresh equity capture must wait for
the regular-session gate. Offline fixtures and a source-copy compatibility pass
do not establish real capture throughput, quote freshness, strategy performance
or online Grok behavior. No automated or scheduled GUI launch is created.
