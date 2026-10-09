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
