# Momentum entry funnel and profit-target review

Reviewed source: `7290504c1bafade493cc5e1fd24b1b4ee9432fce`.
This is an offline source/behavior review, not an observation of the user's
running Windows process. No installed source, settings, admission registry or
prepared observer baseline is changed. No trading strategy is released.

## Where the entry actually waits

| Stage | Source | What must happen | Interpretation |
| --- | --- | --- | --- |
| Discovery | `WarriorMomentumRuntime.discover` and discovery configuration | A candidate passes its current universe, price, gain, participation and tradability checks | Seeing a scanner candidate is not order authorization; source defaults and effective installed settings can differ. |
| Setup timing | `setups.detect_micro_pullback` | Three-bar impulse, a bounded pullback (default minimum two bars), shape/depth/reduced-selling checks, then a completed close above buffered pullback resistance | This can trigger below the previous HOD, but an intrabar touch without a confirming close remains FORMING. |
| Setup selection | `setups.detect_best_setup` | TRIGGERED beats FORMING, then score determines selection | HOD's score 90 beats Micro Pullback's 88 when both trigger. It does not suppress a triggered micro setup merely because HOD is only forming. |
| Technical signal | `runtime.entry_rejections`, `technical_entry_signal` | Triggered valid structure, setup score, positive bounded structural risk, permitted session and tradability; configured entry liquidity and catalyst prerequisites still apply | Momentum-score preference is omitted from the technical path; spread/stale reason labels are not allowed to erase structure. They are assessed again at execution. |
| Freshness | `WarriorForwardCaptureService.observe` | Current source quote/last data and processing/delivery ages pass, or a permitted synchronous quote refresh supplies current evidence | Provider age and processing delay are different causes; deleting freshness protection does not create a valid executable quote. |
| Executable economics | `_assess_executable_opportunity`, `_execution_entry_signal`, `remaining_reward_ok` | Current price remains within displacement bounds, quote quality is acceptable, and original targets retain enough reward after spread allowance | These are current WAIT constraints, not evidence that the underlying setup never existed. Strong momentum cannot make consumed target room reappear. |
| Current trigger confirmation | `_requires_executable_trigger_confirmation` | BID confirms the trigger for the acceleration/reacceleration/reclaim point-based families | This extra check is not universally applied to Micro Pullback or HOD bar-based setups. |
| Account and ownership | autonomous PAPER bridge, account/risk authorization | Valid stop, quantity/risk budget, buying power/exposure, lifecycle and duplicate protections | Preserve these controls; loosening pattern timing is a different decision. |
| Release admission | desktop `production_policy`, `QualifiedEquityTradingService` and bridge entry qualification | Exact reviewed owner/code/config policy approval | The reviewed release registry is empty. A perfect signal still cannot place a new equity BUY. This is intentional quarantine, not a detector defect. SELLs and cancels retain their paths. |

The current runtime still assigns a temporary execution-quality wait to POOR or
TEMPORARILY_BLOCKED quotes. Therefore saying "spread was removed everywhere" is
incorrect: it is a discovery/ranking input and can still cause a later execution
wait. This is separate from a malformed/crossed quote or stale timestamp. The
review does not alter that behavior or claim which condition dominated a past
missed trade.

Three new controlled tests establish concrete behavior:

1. A valid micro-pullback close above local resistance and below prior HOD is
   TRIGGERED and selected as Micro Pullback.
2. The same path touching local resistance intrabar but closing below its
   buffer remains FORMING.
3. A path with simultaneous valid micro/HOD triggers selects HOD and its higher
   trigger because HOD has the larger setup score.

These tests explain possible delay/selection mechanisms. They do not measure
latency or establish the cause of any historical missed entry. A user's specific
candidate needs its contemporaneous setup, quote, lifecycle and gate evidence.

## Profit-taking machinery already present

`observe` updates authoritative position and executable-BID profit evidence
before setup discovery. `_manage_executable_profit_harvest` requires confirmed
inventory, reconciled protection, a fresh executable BID, nonzero remaining
shares and configured peak-R activation. Source defaults include a 0.75R first
harvest of 25% of managed shares, floored to whole shares while leaving at least
one share. Later tiers and a peak-based stop lock also exist. Effective installed
settings must be verified rather than inferred from these defaults.

The usual FIRST_TARGET/SECOND_TARGET bracket is separate from this discretionary
harvest. A harvest can replace a passive first target while its correlated stop
continues to protect inventory. Multiple mechanisms must not be naively added
or independently reserve the same shares. An unfilled target is not realized
profit; a successful-looking submit response without a durable order is not
verified target protection. Tiny positions may not support a floor-rounded
partial, and an old peak is not proof a current profitable fill is possible.

Existing regression suites exercise missing/cancelled target repair, durable
quantity/price/lifecycle verification, false-success submission detection,
incremental entry fills, partial target fills, first-to-second-target progression,
recovered bracket idempotency, contingent-stop behavior and persistence failures.
The executable-BID harvest scenario also verifies a real local PAPER-simulator
fill, harvest quantity, peak lock and duplicate-callback handling. These are
synthetic/local ledger checks, not actual broker fills or real profit evidence.

Validation completed: 124 existing tests across opportunity engine, entry
economics, durable target reconciliation, autonomous PAPER bridge and equity
admission, plus the executable-harvest scenario; all passed. The three new timing
reproductions also passed. Two additional checks confirmed FORMING does not
execute and Scalper projection remains separately owned/generation-bound
(129 total). No GUI was launched by this review.

## Actionable next boundary

Do not delete all safety controls or rewrite already-tested target recovery as
a response to an unverified missed trade. The earlier-entry experiment should
freeze a pre-HOD local-pause trigger and its selection priority as a distinct
Momentum research policy. Evaluate before/after entry timing and whole-share
exits on the same complete mover sessions, including gaps and costs. It must
not inherit an old label's headline win percentage or turn +10% alone into a BUY.

Keep Quick Scalper's trigger, short holding limit, target/cost model and stall
exit independent. Shared delivery, account risk and ownership coordination are
expected and do not make the two engines one strategy. Monday's bounded capture
can establish current native quote/context coverage; it does not by itself
measure all scanner losses or authorize either research policy. Capture hooks
may produce no records when their native paths are not reached; do not fabricate
entries or relax admission merely to fill a diagnostic file.
