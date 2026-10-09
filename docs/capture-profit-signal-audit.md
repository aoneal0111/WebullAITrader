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
