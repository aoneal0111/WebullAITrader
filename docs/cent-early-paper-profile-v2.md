# Separate cent-scalp and native early-entry PAPER profile

`install-paper-day-trial.ps1 -ExperimentProfile CENT_EARLY_V2` prepares a new,
explicit experiment. It does not convert, renew or erase the previous trial.
The old `LEGACY_V1` profile remains the default and its saved specs remain
readable. The ordinary desktop release policy remains qualified-only. Neither
profile is a profitability qualification or a public-repository reproduction.

## Warrior

The V2 native setup allowlist is `MICRO_PULLBACK`,
`MOMENTUM_REACCELERATION`, and `RECLAIM_CONTINUATION`. Selection filters these
names **before** ranking detections, so a higher-scoring HOD setup cannot
crowd them out and then be rejected only at the gateway. Strategy attribution
uses the actual native setup name rather than an overlapping research taxonomy
label. These native rules are not relabeled as the historical FIRST_PULLBACK
research detector. A synthetic native micro-pullback test demonstrates a local
trigger below the prior high of day; no promise is made that every accepted
setup will precede HOD or generate a trade.

## Quick Scalper

The profile selects an entry-relative gross target from $0.05 through $0.10 per
share, rounded up to a whole-cent move. Estimated spread/slippage and native
momentum-confidence discounts remain. A setup waits if the bounded target
cannot cover those modeled costs and at least 1R net reward, or if demonstrated
price movement does not support reaching it. The target never exceeds $0.10 and
the structural stop is not tightened to force approval.

The within-band selection includes the modeled risk/reward floor before
rounding up. The first V2 implementation omitted that floor when selecting the
target, then checked it afterward. It could choose $0.06 and reject it even
when a $0.07 or $0.08 target satisfied the same $0.10 ceiling. Correcting that
selection does not waive quote freshness or the demonstrated-movement check.
The October 30 NCPL geometry shown in the recording summary still waits on
insufficient demonstrated movement after this correction; this is not proof
that correcting target selection alone would have produced a trade.

The selected target move is persisted as `scalp_cent_target_move`. After fills,
the working full-quantity sell LIMIT is anchored to the actual weighted entry
price. Reconciliation retains this behavior from persisted metadata, including
when the adapter is restarted with the ordinary config. Previously opened
positions without this metadata retain their original target logic.

For a cent-profile position, a momentum-stall opinion does not cancel the
working LIMIT target in favor of a market profit exit. Existing protective
stops, profit-defense stop amendments and maximum-hold exits still apply.
Protection and maximum-hold exits can close below the target or at a loss.
Partial fills, quote gaps, real fill quality and costs can differ from modeled
economics. A sell limit does not guarantee execution. No fill is invented or
clamped to the intended profit or stop.

## Evidence and boundaries

Scalper profit-defense/max-hold decisions record their input BID, provider
timestamp, decision time and lifecycle in `EXIT_DECISION`, at most once per
second per position. Canonical SELL fills record `EXIT_FILL`, original exit
reason/type, stop or limit reference, actual fill price and adverse difference.
The same canonical fill ID is counted only once. These are diagnostic facts,
not execution authority; the raw PAPER event remains the ledger evidence.
The exit extractor includes these new record kinds in selected windows.

The V2 trial retains PAPER-only operation, live/Grok order submission off,
dynamic authorization for both native engines, fresh quote checks, account and
ownership checks, capture-health gating and the existing $1 modeled initial
risk, 10-share, $100 position, 50-attempt and eight-hour bounds. Modeled initial
risk is not a guaranteed realized-loss cap. Entry session permissions remain
premarket and regular; extended-hours recording does not grant additional
trading permission. Grok can be started separately for bounded opinions and
never becomes an input that the native execution path waits for.

The installer tests in a detached checkout, creates a backup branch and only
fast-forwards clean installed source. Atlas and Grok must be closed first.
Preparation does not start Atlas, create the lease, make provider/model calls,
change `.env`, or place orders. The operator uses the newly printed Atlas
launcher and clicks START normally. Retain all earlier results and compare
completed-trade payoffs, not win rate alone, across the distinct profiles.

`update-existing-paper-trial.ps1 -Repo ... -Revision <exact SHA> -Trial ...`
updates clean source for an existing, still-valid V2 trial after offline Windows
checks. It requires Atlas and Grok closed, preserves and hash-checks the trial
spec, entry-budget database and sidecars, launchers, recording pointer, pause
flags and `.env`, and does not create a new lease or replenish entry attempts.
An expired trial is refused. Old recordings and Grok results are retained.
The separate Grok launcher still refuses an existing output directory; the
repair reports that condition instead of deleting previous results or starting
another paid invocation.

## October 30 diagnosis boundaries

The supplied run ended at 14:40 UTC (09:40 Central), before its 21:17 UTC lease
expiry. Both native engines were running; the Scalper recorded zero submissions.
Of 2,128 non-executable confirmations, 1,987 reported stale provider data and
141 insufficient edge. These are repeated assessments, not independent trades.
The counter `quick_scalper_stale_preview_refresh_advanced` increments only when
the entire refreshed assessment becomes executable. Zero does not establish
that provider timestamps never advanced. Use the saved paired-confirmation
summary or the exact-source `audit-recorded-scalper.ps1` replay to distinguish
stale stream, stale REST, and REST timestamp regressions before changing a feed.
No cache, timestamp interpretation, source substitution or quote-age threshold
is changed by the target-selection correction. Exit-fill counts alone cannot
establish the run's completed-trade profitability.
