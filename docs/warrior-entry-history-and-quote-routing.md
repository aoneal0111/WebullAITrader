# Warrior entry history and quote routing repair

## Historical changes

These are source-history findings, not an attribution of every missed trade to
one commit, and not evidence that an older revision was profitable.

| Revision | Change affecting Warrior |
| --- | --- |
| aa929917 (August 28) | Added REST execution confirmation for stale observations, including provider last/book freshness and same technical-minute guards. These gates predate the later native PAPER entry work. |
| 7e3a1fcb (September 18) | Bound fresh executable asks to existing structural triggers within the adaptive displacement envelope, avoiding stale passive entry limits. |
| 3d8207d / 43abbdf (October 11) | Bound native entries to asynchronous Grok review and acknowledgement adoption. A technical signal no longer alone authorizes submission when review is configured. |
| c84ca89 (October 23) | Quarantined unqualified strategies. Later explicit PAPER experiments provide source-bound entry authority; retaining the generic quarantine banner does not establish that this specific operation is blocked. |
| 8c41e71 (November 3) | Selected explicit Warrior review/directed control while keeping Scalper native. |
| 4f3f18f (November 13) | Restored all 23 implemented taxonomy families before native gates in the explicit library operation. The fresh-quote branch still rediscovered only the native setup, losing taxonomy-only geometry. |
| d2125be (November 16) | Shared the subscribed PAPER quote source with Warrior and its controller. That selector has Scalper's five-second book age, 250ms future allowance and no upper last-trade age, unlike Warrior's final two-second/zero-future requirement. |

## Proven defects repaired

Warrior and Scalper now keep separate quote selectors fed by the same subscribed
market events. Warrior uses fresh stream evidence only when book, receipt and
last trade satisfy its existing two-second and zero-future constraints. Otherwise
it attempts the existing REST source. Thus an unusable cached stream snapshot
cannot hide a usable REST confirmation. Scalper's selector behavior is unchanged.
The earlier Warrior stale-observation branch uses the selector's existing final
age requirement, so a 2-5 second observation is refreshed before preparation
rather than reaching review only to fail dispatch.

A fresh quote now reruns the same taxonomy selection path as initial assessment
when the operation enables the strategy library and no native setup exists.
Current detections, technical signal, generation matching, account/reward/risk,
Grok review and controller authorization still apply. An invalidated setup is
not retained merely because its prior identity existed. Minute rollover still
requires another assessment with current completed bars.

Execution confirmation validates last/bid/ask prices and each provider timestamp.
It distinguishes missing quotes, invalid values, stale/future bid, ask and last
trade, minute mismatch and obsolete generation. Exact existing execution-gate
counters are now included as recording-scoped deltas in daily summaries; missing
confirmation is also counted. No timestamps are rewritten or book-age limits
relaxed.

## Verification and remaining limits

The taxonomy regression first failed on the old quote-refresh path, then passed
with a real library detector after the repair. Regressions cover a fresh fallback,
unchanged Scalper selection, valid library reassessment, invalidating quotes,
Grok-pending behavior without submitting, and redacted rejection-counter export.
This proves those paths, not a win rate or the exact frequency of each defect
in an earlier run whose detailed diagnostics have not been supplied.

The installer validates and backs up an existing stopped continuous PAPER
operation, tests in an isolated checkout and fast-forwards only. It leaves Atlas
stopped. No model/provider requests, orders, strategy-parameter/spec edits,
ledger edits or crypto changes occur. Optional CaptureStoppedRun captures bounded
candidate runtime diagnostics before installer tests create more files. Candidates
are explicitly not attributed to the recording until their times and counters
are checked. It never assumes the newest diagnostic belongs to trading.
