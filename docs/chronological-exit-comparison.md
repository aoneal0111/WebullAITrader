# Bounded chronological exit comparison

This offline research command compares three explicitly declared long-exit
policies on identical fixed planned entries: structural stop plus time exit,
half at +1R then break-even, and half at +1R then a 1R-distance trailing stop.
It is an exit-mechanics experiment, not an exact replay of Warrior's adaptive
targets, harvest logic, entry authorization, or actual fills. Quick Scalper is
not pooled with these research episodes.

Use a small first batch:

```powershell
& .\.venv\Scripts\python.exe -m app.trade_intelligence.knowledge.exit_replay `
    data/research/historical_tranches/2026_06_01__2026_08_31 `
    --max-scan 100 --max-episodes 5 --hold-minutes 60 `
    --risk-dollars 25 --cost-per-share-per-side 0.01
```

The reader consumes at most the requested episode rows and loads only selected
symbol/day normalized partitions (maximum 5,000 source rows per partition).
Default input limits are 100 scanned rows and 10 matching episodes; hard limits
are 1,000 scanned rows and 100 matching episodes per invocation. Each result
records the source partition hash, declared costs and hold horizon. Integer
shares are sized from the planned entry/structural stop and capped at 10,000.
This does not read the whole 22 GB episode file, acquire data or start Atlas.

## Price and time semantics

The entry is assumed filled immediately before a one-minute bar beginning at
the supplied entry time. A bar overlapping that time cannot establish which
of its extrema occurred after entry and remains unresolved. Subsequent bars
must be contiguous, strictly ordered and belong to the same symbol. Missing
minutes remain unresolved; an IEX missing minute does not prove no price event
occurred elsewhere. Incomplete samples remain open rather than receiving a
future peak or an invented end-of-data exit.

An opening price below the active stop exits at that worse opening price.
Otherwise, stop/target touches in the same bar are unresolved unless the
opening price establishes the target first. Partial targets use their stated
price as a conservative bar proxy. New break-even/trailing levels take effect
on the next bar, never retroactively against the current bar's low. The hold
deadline exits at the next contiguous bar's open. Both entry and exit costs
are charged per share; partial orders round down to whole shares.

## Interpretation

Minute OHLC bars are not consolidated bid/ask quotes, executable liquidity,
partial fills or exact event times. These are simulated bar-price results;
no actual-fill or profitability claim is made. Episodes are independent:
overlap, shared capital, buying power and concurrent strategy ownership are
not modeled. A file-order sample is not a representative performance sample.

Totals include only episodes closed under all three policies, with unresolved
counts displayed. That common cohort can still be selectively biased, so
inspect its coverage before comparing totals. First establish replay coverage
and correctness, then use chronological periods and cost sensitivity. Do not
promote a policy from this small sample or let it control runtime automatically.

## Resumable coverage batches

For coverage across strategies, use a separate research checkpoint:

```powershell
& .\.venv\Scripts\python.exe -m app.trade_intelligence.knowledge.exit_replay `
    data/research/historical_tranches/2026_06_01__2026_08_31 `
    --strategy ALL --max-scan 250 --max-episodes 10 --max-per-symbol 1 `
    --hold-minutes 60 --risk-dollars 25 --cost-per-share-per-side 0.01 `
    --checkpoint data/research/exit_coverage_v1/checkpoint.json `
    --report-dir data/research/exit_coverage_v1/reports --summary-only
```

Repeat exactly the same command for the next batch. The checkpoint seeks
directly to the next source byte offset; it does not reread preceding episode
rows. Source path, size, modification time and policy configuration must match.
A malformed batch does not advance the checkpoint. Report-write failure also
prevents advancement. Reports are written before the checkpoint, using atomic
file replacement. These two writes are not one transaction: after a crash
between them, retrying regenerates the same offset-named report. Run only one
process per checkpoint. Source size/mtime checks detect ordinary edits but are
not a cryptographic integrity guarantee; keep the source immutable.

Each report preserves selected episode details and normalized partition hashes,
per-policy status counts, skipped-row reasons, and date/strategy groups. Overall
totals count each selected episode once. `ALL` records an episode in each of its
strategy memberships; these groups overlap and must not be summed together.
Paired PNL includes only episodes closed under all three policies. A zero total
with zero paired episodes means no comparison, not break-even performance.

The per-symbol cap resets each batch. Capped rows are counted as skipped and
consumed by the cursor; they are not queued for later replay. This deliberately
samples the source order and can omit later opportunities in the same symbol.
It is a coverage diagnostic, not a representative strategy evaluation or a
chronologically sorted portfolio replay. Batch reports are independent, not
cumulative; their byte ranges identify the records consumed. Keep costs,
horizon and selection settings fixed across batches. Use a new checkpoint and
report directory for a changed policy or a separate sampling experiment.

## Opt-in earlier partial comparison

`--policy-set EARLY_PARTIAL_V1` adds two policies to the original three:
25% at +0.75R then break-even, and 25% at +0.50R then break-even. They use
identical entries, quantities, costs and runner handling; only activation
differs. Partials round down to whole shares. Fewer than four shares means
no quarter partial and no break-even activation. New stops still take effect
on the next bar. Default `BASELINE` retains the original three policies and
existing checkpoint configuration.

Start a separate small research batch:

```powershell
Set-Location "$env:USERPROFILE\WebullAITrader"
$ReplayOutput = & .\.venv\Scripts\python.exe `
    -m app.trade_intelligence.knowledge.exit_replay `
    data/research/historical_tranches/2026_06_01__2026_08_31 `
    --strategy ALL --policy-set EARLY_PARTIAL_V1 `
    --max-scan 250 --max-episodes 10 --max-per-symbol 1 `
    --hold-minutes 60 --risk-dollars 25 --cost-per-share-per-side 0.01 `
    --checkpoint data/research/early_partial_v1/checkpoint.json `
    --report-dir data/research/early_partial_v1/reports --summary-only
if ($LASTEXITCODE -ne 0) { throw "Early partial batch failed; stop here." }
$Comparison = $ReplayOutput[-1] | ConvertFrom-Json
$Comparison | Select-Object scanned_rows, selected_episodes, `
    source_rows_consumed, at_eof | Format-List
$Comparison.early_partial_comparison | ConvertTo-Json -Depth 6
```

Repeat exactly this command to resume. Do not reuse `exit_coverage_v1`'s
checkpoint or reports. The early-policy checkpoint records all five policy
definitions and rejects changed settings. Its `early_partial_comparison`
totals use only episodes closed under both quarter-partial policies. Overall
`paired_pnl_totals` still require all five to close; these are different
cohorts and must not be combined. Unresolved paths are counted, not assigned
zero profit. Each report's episodes preserve the fill traces for inspection.

This is a bar-mechanics comparison, not an exact Warrior 0.75R harvest replay:
the experiment uses break-even, whereas Warrior also has staged harvest and
peak-retention logic. It does not run the Quick Scalper or modify runtime
configuration. No winner has been established. Earlier sales can preserve
profit before a reversal and reduce profit on a continued winner; both are
covered by tests. The sparse historical corpus can still leave most episodes
unresolved, so this batch measures coverage before performance conclusions.

## Explicit entry and sizing experiments

The default remains `PLANNED_PRE_BAR` with `PRICE_RISK` sizing. Existing
checkpoint configurations and commands remain compatible. It assumes the
planned entry filled immediately before the detection bar; it does not
establish that an order could have obtained that price.

`--sizing-mode COST_INCLUSIVE --capital-cap 2500` sizes integer shares from
`risk_dollars / (entry_price - structural_stop + 2 * cost_per_share_per_side)`.
It also caps shares at `capital_cap / (entry_price + cost_per_share_per_side)`
and 10,000. Fractions round down. An explicit finite positive capital cap is
required for this mode. $2,500 is an illustrative **per-episode research cap**,
not a read of account buying power or a recommended allocation. Optional
`--capital-cap` also works with price-risk sizing. Reports record purchase
notional, entry cash including estimated cost, planned price risk, both cost
sides, and their total planned stop loss. Gap losses can exceed that budget;
the cap does not model concurrent positions or shared capital.

`--entry-model NEXT_MINUTE_OPEN` is a separate entry experiment: take the open
of the exact first minute boundary strictly after the episode's detection
timestamp. Ignore earlier bar extrema, use the new entry price for risk and
targets, retain the original structural stop, and start the hold clock at the
new entry. It is not a simulated fill of the original limit order. OHLC opens
do not establish spread, quote freshness, executable liquidity, latency or
actual fills. This does not validate the historical detector itself.

If that exact minute is absent, do not jump to a later available bar. If its
open is at/below the structural stop, do not assume an entry followed by an
instant stop loss. These episodes receive explicit `NO_ENTRY_*` statuses,
zero quantity/cost/fills and null PNL, remain in selected coverage, and are
excluded from paired profit totals. A zero affordable next-open quantity is
treated the same way. In the planned-entry mode, unaffordable/out-of-bound
quantities retain the existing `QUANTITY_OUTSIDE_LIMIT` skipped-row behavior.

Compare one change at a time with three separate checkpoints, retaining the
same source order, batch settings, hold, risk, cost and partial-policy set:

| Experiment | Entry model | Sizing | Example cap |
| --- | --- | --- | --- |
| Original | PLANNED_PRE_BAR | PRICE_RISK | None |
| Sizing only | PLANNED_PRE_BAR | COST_INCLUSIVE | $2,500 |
| Entry and sizing | NEXT_MINUTE_OPEN | COST_INCLUSIVE | $2,500 |

Use a fresh checkpoint for each. Changing entry model, sizing mode or capital
cap rejects a saved checkpoint; policy definitions are recorded too. Resume
with exactly the same settings. Overall profit comparisons require inspecting
matching episode IDs and coverage: different entry eligibility or quantities
make raw totals across experiments insufficient to attribute improvements.

First run the new entry/coverage diagnostic in a small batch:

```powershell
$ReplayOutput = & .\.venv\Scripts\python.exe `
    -m app.trade_intelligence.knowledge.exit_replay `
    data/research/historical_tranches/2026_06_01__2026_08_31 `
    --strategy ALL --policy-set EARLY_PARTIAL_V1 `
    --entry-model NEXT_MINUTE_OPEN --sizing-mode COST_INCLUSIVE `
    --capital-cap 2500 --max-scan 250 --max-episodes 10 --max-per-symbol 1 `
    --hold-minutes 60 --risk-dollars 25 --cost-per-share-per-side 0.01 `
    --checkpoint data/research/entry_sizing_v1/checkpoint.json `
    --report-dir data/research/entry_sizing_v1/reports --summary-only
if ($LASTEXITCODE -ne 0) { throw "Entry/sizing batch failed; stop here." }
$Comparison = $ReplayOutput[-1] | ConvertFrom-Json
$Comparison | Select-Object scanned_rows, selected_episodes, `
    source_rows_consumed, at_eof | Format-List
$Comparison.entry_status_counts | ConvertTo-Json -Depth 4
$Comparison.early_partial_comparison | ConvertTo-Json -Depth 6
```

To isolate sizing, change `NEXT_MINUTE_OPEN` to `PLANNED_PRE_BAR` and use
`data/research/sizing_only_v1/` for both checkpoint and reports. Do not reuse
`early_partial_v1` or `exit_coverage_v1`. These changes are confined to offline
research and do not alter Atlas runtime entry, risk or exit rules.
