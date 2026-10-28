# Frozen historical equity price study

This is a new price replay, not another reuse of stored target-hit labels.
The previous ordered-label inventory identified HOD reclaim, breakout/retest,
HOD breakout and ORB as stronger descriptive leads than first pullback. Those
five historical families are included, with FIRST_PULLBACK as a control. This
is momentum-family research; it does not activate the separate cent scalper or
reproduce any external repository's published performance.

## Fixed experiment

Before any price results, the study writes a frozen plan and hashes its two
implementation files. The source is the reconciled repaired corpus with SHA-256
`faeb26493d4c836c36d6daa4665925ec0cef3607af024676f13bc43d688ac73a`.
A single streaming pass verifies that digest and the report's row/membership
counts and indexes only compact selected-family signal records. Stored forward
outcomes and target-hit flags are not copied into the index or consulted.
Source bar-window timestamps must describe bars completed by detection; a
future or missing prefix blocks that entry. This is a timestamp check, not an
independent regeneration of all detectors or point-in-time features.

For each of June, July and August 2026, choose up to 200 symbol-day partitions
by the lowest SHA-256 of the version, symbol and date. All selected-family
signal episodes in those partitions are evaluated. Selection uses no profit,
future excursion or target label, but this is still a retrospective case sample
of a mined universe, not deployable point-in-time universe selection. The dates
and aggregate family results were already examined; they are not fresh holdout.

Each family is compared across four methods: full exit at 0.5R; full exit at 1R;
half at 0.5R then price break-even; half at 1R then price break-even. Remaining
shares have a sixty-minute holding deadline. Break-even is before costs and
need not produce a net profitable trade. R is actual entry price minus the
source structural stop, not a cent target. Partial share quantities round down.

Entry is the exact next minute's OHLC open after the detection minute. A missing
next minute, invalid geometry, or an open through the stop receives no entry.
Late entry risk, target and position size are recomputed from that open. It does
not assume a fill at the earlier trigger price. The per-side all-in friction
allowances are fixed at $0.01 and $0.02 per share, applied by the reused exit
engine; they are assumptions, not verified Webull fees or observed spreads.

## Shared capital within each independent day

Every family/exit/cost case starts each sampled calendar day with a separate
$1,000 virtual wallet. It allows at most two positions, no same-symbol overlap,
up to $500 per entry including entry cost, and $10 planned stop risk inclusive
of the assumed round-trip costs. Integer size is also limited to 1% of the
preceding completed minute's volume, never the entry bar's future volume.
This volume allowance is not proof of available quotes or executable size.

Entries are ordered in UTC, then by symbol and episode ID. Fully closed exits
known by that timestamp settle first. An intrabar fill becomes known only at
that bar's end; cash is not reused earlier. Full original entry cash remains
reserved until the whole position closes, so partial proceeds cannot fund an
extra entry. This is conservative and can differ from a live cash account.

The same mechanics as `knowledge.exit_replay` handle stops, gap opens, full and
partial targets, next-bar break-even stops and whole shares. The common classes
and functions were extracted unchanged to `episode_exit_mechanics.py`; the old
CLI re-exports them and retains its interface. Stop/target conflicts and missing
minutes stay unresolved. Open or partly closed positions at the end of a file
are never given an invented liquidation price. Their cash stays reserved for
that day and later same-day opportunities can be blocked.

These are independent daily experiments, not one continuously funded June–August
portfolio. There is no cross-day reuse or restoration of unresolved capital
within a continuous-return calculation. No CAGR, total-account return or marked
equity drawdown is reported. Never add results across alternative cases as
though they were independent trades or simultaneous funded accounts.

## Coverage and output

Every case reports closed outcomes on its own completely resolved, price-valid
days plus conditional closed results from all days and unresolved positions.
For comparing exit/cost methods, use `paired_common_day_metrics`: those use the
same dates that all eight methods/costs for that family completely resolved.
The exact dates are listed. This common-day restriction is itself coverage
selection, not proof of profit; excluded days must always be considered.
Missing/invalid price partitions exclude the affected family-day from headline
results. Strong conditional results cannot qualify a strategy.

The summary reports all forty cases and monthly descriptive breakdowns. Private
JSONL traces retain entry prices, sizing, fills and unresolved positions; private
per-day results and input-file hashes stay local. A bounded LRU cache holds at
most 32 price partitions. Files are checked for order, OHLC geometry, identity,
size and stability. Maximums are 40 GB / two million source rows, 1 MB per source
row, 8 MB / 3,000 rows per price partition, and 50,000 selected events per day.
A changed source digest or changed cached price file fails the study. No paid
requests, broker access, credentials or policy changes occur.

Run `tools/replay-historical-equity-prices.ps1` at an exact reviewed revision.
It verifies existing and new exit checks first, then uses an isolated research
checkout. Do not launch Atlas through this tool. The actual operator corpus and
prices are unavailable to the development host; its local checks use synthetic
mechanics fixtures and do not claim strategy performance. Attach only
`episode-price-study-summary.json`. Preserve `failure.json` if the run fails.

## Completed-timestamp adapter correction

The first price-study adapter treated `bar_window.timestamp` as an opening
stamp and added one minute. The actual producer, `mining._candidate`, writes
`CompletedBar.completed_at` into the episode's HistoricalBar.timestamp field.
That field is already completed at detection; normalized partition timestamps
are instead minute opens. The extra minute rejected valid source prefixes and
made the initial study non-evaluable. Synthetic fixtures had not exercised the
real producer's boundary convention.

The corrected adapter requires matching decision-cutoff provenance, ordered
aware completed timestamps no later than detection, and a canonical digest of
the entire stored OHLCV prefix. In memory only, those completed timestamps are
mapped to normalized open stamps one minute earlier. The sampled normalized
bars must reproduce the count and digest before entry is allowed. No corpus,
normalized prices, provider timestamp or trading freshness policy is rewritten.
Future bars, schema gaps or price mismatches still block evaluation.

The correction adds `prefix_adapter` to the frozen plan, with adapter identity
`COMPLETED_EPISODE_TIMES_V2_NORMALIZED_OHLCV_DIGEST_MATCH`. It keeps the original
V1 selection seed, all five families, costs, exit rules and wallet settings.
Therefore it selects the same symbol-days rather than searching for a new
sample. The source must be reindexed because the old compact index retained
only a failed boolean, not the history needed for a trustworthy repair.

`evidence_status` and `prefix_validation` distinguish a replay with entries from
a run lacking validated prefixes. Days with unverified signal evidence are
excluded from headline and common-day comparisons, with counts retained.
`fully_resolved_days` alone describes position resolution;
`headline_complete_days` additionally requires usable price and signal evidence.
A real-miner regression generates corpus episodes using `build_corpus` and tests
completion-time boundary acceptance, normalized OHLCV equality, mutated-price
rejection and future-bar rejection. Prior non-evaluable output is preserved.
