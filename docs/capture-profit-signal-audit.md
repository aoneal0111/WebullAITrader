# Reconciled capture profit signal audit

This bounded offline tool compares observed small-profit protection signals with
actual Warrior paper fills. It does not alter runtime rules, submit orders,
simulate candidate fills, or promote a policy. Scalper records are excluded.

Run from the project root, using the existing read-only capture export:

```powershell
& .\.venv\Scripts\python.exe -m app.trade_intelligence.knowledge.capture_profit_audit `
    data/research/warrior_capture_audit_v1/naut_cci_capture.json
if ($LASTEXITCODE -ne 0) { throw "Capture audit failed." }
```

Defaults are an illustrative 0.40R activation and retention of 50% of the
observed peak total profit. These settings were chosen after inspecting the
selected NAUT example: this is an in-sample diagnostic, not an optimized,
validated, or profitable policy. R uses actual average entry and the recorded
initial structural stop, excluding costs from the denominator.

## Evidence requirements

Orders and captured fills must reconcile by campaign and lifecycle, timestamps,
order identity, side, quantity and price. All entry fills must have one consistent
structural stop. The lifecycle must be closed and whole-share fills valid.
Duplicate orders, invalid settings and excessive input sizes are rejected.
Overlapping entry and exit phases are unsupported, including simultaneous
entry/exit fills whose order is ambiguous. Input limits are 8 MB, 5,000 capture
records, 500 orders and 20 captured Warrior lifecycles.

Quotes are processed in evaluation-time order. Signed age is recomputed from
source timestamps; future quotes are excluded even when a recorded age is zero
or a future flag is false. Stale quotes, crossed/missing sides, observations
outside the fill lifetime and quotes simultaneous with fills are excluded.
Arming waits for all actual entry fills. No later peak can retroactively arm
an earlier signal.

Total profit marks include realized partial proceeds, recorded commissions and
remaining shares valued at the sampled bid. The candidate is an overlay on the
actual management path, not an independently simulated strategy. A declaration
of additional cost per share per side can be provided with
`--extra-cost-per-share-per-side`; it is reported separately from recorded fees.
`candidate_fill_pnl` is always null. Missing intervals mean the tool identifies
the first **observed** signal, not the first true threshold crossing. Bid size,
execution latency, fill quantity, future market impact and all-in broker costs
are not established by this export.

## Selected October 9 capture findings

All three selected lifecycles reconciled; recorded paper commissions were zero.
These are selected cases, not a representative sample or portfolio backtest.

| Lifecycle | Actual closed paper PNL | Sampled total profit peak | Experimental observed exit signal |
| --- | ---: | ---: | --- |
| CCI `f1c22e...` | +$21.70 | +$26.04 | None |
| NAUT `146293...` | +$28.85 | +$33.70 | None |
| NAUT `784dad...` | -$42.24 | +$19.20 | 16:54:15.199777 UTC, bid $2.420, marked profit +$7.68 |

The second NAUT trade bought 384 shares at $2.400 with a $2.290 stop. Its
sampled peak was approximately 0.455R. Neither 0.75R nor 0.50R activation would
have triggered on the eligible captured bids. Its stop stayed at $2.290 and
its first target was canceled unfilled. This supports investigating protection
below the existing harvest threshold; it does not prove the experimental exit
could sell 384 shares at $2.420 or realize the marked profit.

CCI and the first NAUT trade already realized partial profits and tightened
stops. Total profit marks here differ from a full-initial-position mark used
by some management diagnostics. Do not compare those metrics as equal or sum
individual sampled peaks and call that a portfolio peak.

## CCI timestamp discrepancy

CCI has 120 captured quotes whose source timestamp is ahead of evaluation time,
by 3.541 to 57.894 milliseconds. The audit excludes them, rather than treating
clamped zero age as proof of freshness.

In `desktop_sidecar.py`, the full evaluation path anchors `evaluated_at` before
reading later adapter state. Its observation can combine earlier bid/ask values
with that later state's quote timestamp. The lightweight path reads state before
anchoring evaluation time. `forward_runtime.py` also accepts raw quote diagnostics.
Current capture payloads do not identify which path emitted each quote. A newer
adapter state after the evaluation anchor and provider/local clock skew are
possible explanations; the existing export cannot establish the precise cause.
A future diagnostic should capture path provenance and consistent source/state
identity before any runtime timestamp adjustment is considered.

## Next decision

Collect an unselected set of closed Warrior lifecycles, preserve executable
quote sizes and timing, and compare signals with the actual harvest/protection
path. Test different regimes and days with settings frozen before observation.
Scalper needs its own evidence and exit policies. This audit alone does not
justify changing either strategy's runtime thresholds.

## Bounded batch exporter

`capture_profit_export` replaces the pasted export script. It selects closed,
filled Warrior lifecycles from the active campaign whose entry creation time is
at or after the supplied performance snapshot's runtime start. Sorting is by
entry time and lifecycle, not by PNL. It reads both SQLite databases in `mode=ro`
with `query_only` enabled and a ten-second progress deadline per connection.
It never starts Atlas, modifies orders, or changes configuration.

```powershell
& .\.venv\Scripts\python.exe -m app.trade_intelligence.knowledge.capture_profit_export `
    $Latest.FullName --offset 5 --limit 2
if ($LASTEXITCODE -ne 0) { throw "Capture export failed." }
```

Limits: at most five lifecycles per call, 5,000 active-campaign orders / 32 MB
for the streamed order scan, 500 orders per selected lifecycle, 4,000 execution
path records / 8 MB, and 1,000 nearby entry context records / 4 MB. Every capture
export is capped at 8 MB. Overflow is reported as an exclusion, not silently
truncated. No usable exports produces exit code 1; partial success remains exit
code 0 with exclusions listed in stdout and the archive manifest. The manifest
also includes reconciliation status, the fixed audit settings, selected lifecycle
identities and counts. Outputs use a new uniquely named directory and ZIP.

Each lifecycle includes nearby DECISION, DI_ENTRY_DIAGNOSTIC,
EXECUTION_GATE_DECISION, SETUP_LIFECYCLE, STATE_TRANSITION, SPREAD_EVIDENCE and
DATA_QUALITY records from five minutes before entry creation to thirty seconds
after. These are **symbol/time candidates for investigation**, not proven joins
to the authorizing decision. They are not filtered by campaign, because older
context records may omit campaign identity. Check identity, timing, session,
retained trigger and confirmation age before drawing an authorization conclusion.
Future-of-entry records are context only, never pre-entry evidence.

Offsets are positions in the currently eligible closed set, not a durable
checkpoint. If additional trades close between calls, its ordering can change.
Use a stopped/fixed run for a stable batch sequence, keep the manifest and deduplicate
by campaign/lifecycle. These retrospective, closed-trade samples are not forward
validation and do not include trades still open. Do not mix runtime versions
without checking available provenance.

### Broader October 9 batch

The first five closed trades in entry order all reconciled: CCI +$21.70,
FSLY -$43.92, IBRX -$6.57, first NAUT +$28.85 and JAGX -$17.00. The unchanged
0.40R overlay produced no observed exit signal for any of these five. FSLY's
eligible sampled peak was about 0.20R; IBRX's was 0.33R; JAGX's highest eligible
sampled mark was negative. The overlay cannot address every weak entry or stalled
trade. CCI and first NAUT already realized partials and raised their stops.

A newer detector state of FORMING does not independently prove that an entry
bypassed confirmation: Warrior intentionally permits a bounded retained earlier
trigger, subject to current executable bid confirmation and other gates. The
retained-confirmation expiry repair is already documented in
`retained-trigger-expiry.md`. Nearby entry evidence is needed to evaluate the
specific generation and confirmation age used for FSLY/JAGX. Do not infer a new
runtime defect from proximity alone.
