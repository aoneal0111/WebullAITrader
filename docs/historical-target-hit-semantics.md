# Historical target hits are not realized trade win rates

`metric_semantics_audit.py` checks the saved `full_research_final.json` ZIP without
opening installed databases or importing the GUI/scientific runtime. It retains
all strategy names, whole-sample 0.5R/1R/2R cohorts and chronological
TRAIN/VALIDATION/TEST 2R cohorts. It never ranks, changes, or releases policies.

The historical aggregation code in `app/trade_intelligence/knowledge/analysis.py`
uses SQL averages of separate hit and stop-first flags in `_sql_metrics` and
`_sql_policy_metrics`. `_policy_columns` selects matching target columns. The
observation insertion stores the target hit flag independently of whether
`first_plan_event` equals `STOP_FIRST`. Therefore eventual hits and stop-first
rates may overlap. A target touch after an earlier stop is not a winning trade.
The original report's literal rate labels are preserved by this diagnostic;
replacing their names with `win_rate` would change their meaning incorrectly.

For a common population, let H mean eventual target hit and S mean reported
stop-first. From their marginal probabilities alone:

- P(H and S) is at least max(0, P(H) + P(S) - 1).
- P(H and not S) is at least max(0, P(H) - P(S)).
- P(H and not S) is at most min(P(H), 1 - P(S)).

These are probability bounds, not confidence intervals, estimated win rates,
expected returns, or a substitute for a sequential replay. They require the
same cohort and target denominator, as in the cited source columns. For example,
in an invented population with 80% eventual hits and 60% stop-first observations,
at least 40% overlap and at most 40% hit without stop-first. The exact overlap
cannot be reconstructed from two aggregate rates. Ambiguous candles, incomplete
outcomes, gap/slippage losses and costs require separate evidence.

Run directly, preserving private output locally:

```powershell
& .\.venv\Scripts\python.exe app\equity_overhaul\metric_semantics_audit.py "C:\path\original-report.zip"
```

The checker accepts one bounded JSON report member, validates finite probability
ranges and strategy membership, hashes the archive and report, and returns
`realized_win_rate: null`, `execution_authorized: false`, `promotion: NONE` and
an empty rankings list. Empty cohorts and unavailable rates stay unavailable.
No user strategy statistics or performance records are committed with the code.

## Separate research tracks

Momentum's existing `PRE_HOD_PULLBACK_10PCT_V1` is an independently specified
pre-HOD trigger hypothesis, not a reuse of the historical `MICRO_PULLBACK` or
`FIRST_PULLBACK` labels. Its early trigger and partial exits must be replayed
as their own rules on causal small-cap mover history. Negative evidence about
an old detector does not establish the result of the replacement detector.

Quick Scalper retains its own short-horizon entry, executable-quote cost model,
maximum hold and stall management. A whole-sample 0.5R hit statistic from a
Momentum family does not validate a five-minute Scalper. Likewise the separate
`VWAP_RECLAIM_SCALP_V1` candidate has no qualification merely because it exists.

Previously examined TEST periods remain descriptive. Do not optimize or select
on them and call them a new holdout. Overlapping family memberships are not
independent trades. Raw historical percentages must not bypass current equity
qualification, freshness, risk or protective exits. This diagnostic does not
change Monday's prepared PAPER observer source or settings.

Validation: seven standalone tests cover all 255 nonempty bounded integer contingency
tables, overlapping outcomes, non-unique
joint probabilities, unavailable rates, invalid probabilities, input immutability
and membership mismatch. The existing ten history-readiness tests also pass.
