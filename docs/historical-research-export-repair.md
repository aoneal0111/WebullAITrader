# Historical evidence and return semantics

The September corpus report stores chronological strategy metrics at
`temporal.splits.TRAIN|VALIDATION|TEST.<strategy>`. The artifact builder now
copies counts and TEST percentage-target/stop-first rates from that structure.
Older strategy-first scorecards remain supported. Execution-analysis counts
are not substituted for outcome sample counts. Policy stability copies the
report's `descriptive_status`; absent walk-forward conclusions remain absent.
Percentage-target rates are distinct from R-target rates.

The old partial-exit calculation credited runner inventory with one-hour MFE,
and could credit an eventual target even after STOP_FIRST. Future maxima are
not terminal fills. Research now resolves a full-target outcome or a stop-first
loss with valid entry/stop geometry. Partial-runner outcomes remain unknown
without chronological exit evidence. Aggregate return metrics remain null
when any observation is unresolved; resolved-subset means and counts are
explicitly separate. Runner MFE is retained as movement evidence rather than
session-close return. Costs, gap slippage and actual fills are not inferred.

This is not a completed chronological runner simulator. Ordered bars/quotes
and a declared exit policy are needed for that comparison. Existing reports
are not rewritten by updating code. New artifacts include an explicit
MFE-versus-realized-return limitation for legacy report payloads.

Desktop PAPER composition creates a HistoricalDecisionIntelligence observer
using this corpus's default database path. HistoricalPaperEntryTimingPolicy is
configured separately; its enablement/mode depend on operational configuration.
Source wiring alone does not prove the current Windows session enabled a
treatment. No configuration, entry policy, exit policy, or trading database
is changed by this patch. Rebuilding evidence can affect a separately enabled
PAPER treatment, so review its configuration before switching artifacts.

## Small-batch verification

Run artifact/runtime/entry-timing tests separately from knowledge analysis tests.
Build a candidate SQLite artifact from the existing 9 MB final JSON into a new
filename, validate its source hash, and inspect chronological parity before
replacing the active artifact. Do not rerun the 22 GB corpus to repair the export.
The existing report's optimistic return fields are legacy movement evidence;
do not use them to select a profitable policy.
