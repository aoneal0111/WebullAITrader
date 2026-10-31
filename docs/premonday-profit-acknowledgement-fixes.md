# Profit-order acknowledgement and setup-continuity repairs

A bridge exit response can be `WORKING` with a failure reason because it retained
an older protective/triggered order instead of accepting the requested new
order. Previously, several consumers interpreted that broad protection status
as a successful new action. This could latch a pending harvest, publish a stop
lock above its confirmed level, or report a rejected Scalper defense as active.
Legacy false/None callbacks also incorrectly replaced active fill attribution.
Session-close/capability-loss requests likewise no longer report a retained
conflicting order as a newly submitted liquidation.

`PaperExitSubmissionDecision` now separates `protection_active` (unchanged broad
meaning) from `requested_order_active` and `request_succeeded`, both requiring no
failure reason. Momentum uses the requested-action predicates for harvests,
profit-defense/trailing amendments and exit-role attribution. Failed requests
retain the previous confirmed stop/role and remain retryable. Scalper uses them
for target readiness, defense events and exit-request transitions; failed actions
do not latch PROFIT_DEFENSE_ACTIVE. An already-triggered or partially filled
protective STOP still remains EXIT_WORKING even if a replacement is refused.
The patch does not cancel that stop or weaken its recognition.

A separate continuity defect checked `setup_type` on a cached tuple rather than
its setup member. Point-based acceleration/reacceleration/reclaim FORMING states
could therefore be resurrected by the bar-pattern continuity fallback after the
point detector stopped selecting them. The repaired check reads the actual
setup and clears that stale cache. Valid bar-pattern FORMING continuity remains.
This is not a new trigger rule or proof of a past missed order's cause.

Regression tests first reproduced the defects. They include a real local PAPER
bridge returning WORKING plus CANCELLATION_FAILED while retaining its old stop,
failed harvest and peak-lock retries, false legacy responses, acknowledgement
state/failure combinations, separate Scalper failure paths and all three point
families. Successful existing target/stop/restart behavior remains covered by the
broader suites. All fills in these tests are synthetic/local; no broker request
or GUI launch is needed. No private performance is published.

The reviewed qualification registry stays empty. There is no newly approved
strategy, no intrabar trigger change, and no change to HOD-vs-micro scoring,
spread/reward thresholds, hard risk controls, live/Grok flags or model wiring.
These correctness repairs do not establish profitable strategies.

## Windows installation

`tools/apply-premonday-repairs.ps1 -Revision <reviewed full SHA>` checks that Atlas
is closed, refuses tracked local edits or a divergent branch, records a backup
branch, and fast-forwards only. It runs focused Windows checks before preparing
a new observer source snapshot. It does not reset the repository on failure or
start Atlas. Installed Windows verification remains pending until the operator
runs it. No credentials are entered or settings edited.

Because installed source changes, the old observer receipt must not be reused.
The installer passes a new explicit LocalAppData output directory to preparation,
keeping it outside OneDrive. The preparation wrapper now accepts an optional
`-OutputDirectory` and verifies the generated launcher/bootstrap/receipt/source
exist before reporting success. This is a persistent-output workaround, not a
proven diagnosis of why the earlier OneDrive directory disappeared. Preparation
still never launches Atlas, and start remains guarded to a regular session.
The original prepared directory is left untouched and will refuse changed source.

## Completed development checks

The final combined run passed 826 tests with one skip (an external historical
corpus was not installed). It covers the complete Warrior suite, native PAPER
bridge, release admission, desktop composition and observer-session preparation.
The eight original regression failures were reproduced before fixing them;
Scalper failure cases were also reproduced before its acknowledgement change.
Additional recovery assertions passed after the changes: a failed Momentum lock
can retry through the real local bridge and produce the confirmed durable stop,
and Scalper failure paths can accept a later successful action.

A full source-copy preparation check was also run without a GUI. With absent
local configuration it correctly refused the default TEST environment; explicitly
PAPER/live-off/Grok-review-off process flags were then used for the development
compatibility check. No installed user configuration was read or edited.
