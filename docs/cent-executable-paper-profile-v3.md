# Explicit cent-scalper repair profile V3

`CENT_EXECUTABLE_V3` is a new, opt-in PAPER experiment. It does not promote a
strategy, rewrite an existing trial, renew an expired lease, or enable Grok.
Atlas remains a visible application launched manually by the operator.

## Evidence and changes

A closed recorded audit reproduced the native policy results. REST confirmation
can return a book older than the stream preview; successful HTTP requests alone
therefore do not establish executable quotes. The report does not identify the
upstream cause of old provider book timestamps or establish data entitlements.

V3 changes two entry calculations, leaving V2 behavior selectable:

- A contiguous equal-BID trough with a strict preceding decline and a later,
  distinct provider-time bounce is confirmed support. The last trough timestamp
  is retained. An unbounced trough, same-time callbacks, broken support or a
  monotonic rise never supplies support. No price or provider time is invented.
- Cent targets already attach to weighted actual entry fill. The prospective
  reward calculation now reserves exit slippage against that fill-relative
  target, while entry slippage increases modeled stop risk. Spread remains in
  the acquisition-cost floor and the required BID travel; it is not deducted
  again as though the target were anchored to an earlier BID.

Let `s` be modeled per-side slippage including the existing size penalty,
`a` ASK, `b` BID, `z` stop, `t` target increment and `R` required net reward:

| Check | V3 arithmetic |
| --- | --- |
| Full acquisition cost floor | `(a-b + 2s) * execution_cost_multiple` |
| Modeled initial stop risk | `a+s-z` |
| Minimum fill-relative target for R | `(a+s-z)*R+s` |
| Modeled target reward R | `(t-s)/(a+s-z)` |
| Required BID travel | `a+t-b+s` |

Targets remain whole cents from $0.05 to $0.10. There is no new spread/RVOL
hard gate, assumed future velocity, lowered fee/slippage estimate, relaxed
provider-age limit or forced trade. This is a conservative model, not a
prediction of attainable fills. Actual fills and fees remain the outcome
source. Existing position targets/stops are not recalculated by this profile.

The shared execution quote parser also refuses explicitly mismatched symbols;
multi-row responses must contain exactly one matching symbol. It never merges
prices/times across rows. The legacy one-row/no-symbol shape remains supported.
This correctness fix applies to all callers; no evidence attributes the observed
143 stale confirmations to a symbol mismatch.

## Capture and blocker checks

Every refused Scalper confirmation now queues a
`SCALPER_CONFIRMATION_DIAGNOSTIC` journal record with exact reason, original
preview and confirmation times, book regression, provider ages and edge terms.
It bypasses fresh-context sampling, so an old quote cannot erase the reason it
was refused. Writing remains on the existing bounded journal thread. There are
no additional provider requests or model calls.

`app.asset_modules.scalper_repair_replay` verifies the closed journal, unchanged
baseline and approved native source hashes, reproduces baseline decisions, and
compares fill-anchored economics on identical snapshots. It fails on unknown
source, changed evidence or a baseline mismatch. The exact prior V2 source
hashes are pinned in the module. Old missing additive config fields are defaulted
to their explicitly reviewed OFF behavior only.

The comparison cannot infer plateau support from a single recorded snapshot.
That requires the original distinct updates; the runtime fix is verified by
native stream and durable gateway tests. The comparison is not a PnL backtest,
win-rate estimate or authority to submit orders. Repeated events are not unique
opportunities. Stale quotes remain stale in both cases.

## Operator sequence

Close Atlas and the old Grok intentions process normally. Install using
`tools/install-paper-day-trial.ps1 -ExperimentProfile CENT_EXECUTABLE_V3
-RecordedSession <closed-session> -Revision <reviewed-commit>`.
The installer tests in a separate worktree, performs the frozen blocker
comparison before merging, creates a backup branch, and prepares unique manual
launchers. It does not change `.env`, start Atlas, make API requests, purchase
subscriptions, or touch prior trial ledgers.

The new parent trial starts only on manual launch; the old expired trial is not
renewed or reset. Caps remain 8 hours, 50 shared entry attempts, $5,000 cumulative
entry notional, 10 shares/$100 position/$1 modeled initial risk. Capture failures,
pauses and expiry stop new entries while independent position protection stays
available. Grok intentions remain non-executing; a separate explicitly prepared
Grok execution lease is still required for native order review.

Review the compact `scalper-repair-replay.json` before the next market session.
Do not expect this change to repair missing/frozen upstream BBO data: the feed
path remains a blocker wherever both stream and REST are stale. Do not retime
old prices or widen the freshness limit to make the status green.

## Verification

Local regression: 2,396 passed, 1 skipped and 16 subtests passed across Warrior,
equity overhaul, asset modules, desktop composition, workstation and session
validity checks. This includes native V2/V3 stream entry through durable PAPER
fills, protection and exits; unchanged V2 behavior; unconfirmed/broken support;
stale quotes; quote symbol identity; closed-journal tampering; and Grok parent
lease/call-budget boundaries. The recorder also uses its supplied clock for
parent-trial validation, keeping capture and expiry checks on one time source.
PowerShell has not been executed locally; the installer runs its focused Windows
checks before applying source changes. No GUI or paid API was started in testing.

### Windows source-binding compatibility

The replay accepts exact LF and Git CRLF hashes for the pinned prior V2 files,
including checkouts where the two files use different endings. The original
manifest hashes and journal are never changed. Current reviewed source receives
the same exact-byte variant check. Unknown hashes, changed code, incomplete
journals and changed policy remain refused. The prior schema is still enforced
for either historical line-ending variant.

A refused comparison now writes a small report identifying the specific failed
check instead of combining scope, hash, policy and completeness checks into one
error. The installer still stops before merge, backup/trial creation or settings
changes. The review worktree and failure report remain available for inspection.
