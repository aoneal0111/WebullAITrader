# Connect historical replay batches to engine comparison

`app.trade_intelligence.knowledge.replay_comparison` reads the `coverage_*.json`
batches already produced by `exit_replay`. It creates versioned Warrior policy
rows for `app.asset_modules.strategy_comparison` and prints the paired summary.
It preserves source/configuration identities, partition digests, byte ranges
and each original unresolved/no-entry reason in the optional output artifact.

All batches must use identical replay settings and source identity. Changed
costs, entry model, sizing, risk, capital cap or policy definitions are separate
experiments. Duplicated/overlapping batches, duplicated episode results and
changed partition digests are refused. Gaps between batch byte ranges are
flagged; file-order batches are not a representative performance sample.

Results remain MINUTE_BAR_PROXY. This adapter does not fetch historical data,
inspect the original bars, verify declared hashes against source files or
reconcile actual fills. It only checks exported report consistency. It does not
simulate fills, change exit rules, start Atlas or promote a policy. Use actual
capture audits separately; an observed profit-defense signal is not an actual
counterfactual exit. These partial-exit mechanics are not the exact Warrior
profit-harvest engine.

The default compares all exported policies using structural-stop-and-time as
the reference. To compare only the two early partial thresholds, explicitly
select both and choose the reference as below. PnL is taken from the existing
replay results, with declared per-share costs already included, not recomputed
from peaks. Unresolved results stay null. Paired totals exclude an episode if
any selected policy did not close. This may introduce coverage selection bias.

## Windows: combine your existing entry-sizing batches

```powershell
Set-Location "$env:USERPROFILE\WebullAITrader"
$Reports = "data/research/entry_sizing_v1/reports"
if (-not (Test-Path $Reports)) { throw "Replay report folder missing." }

$Output = Join-Path $env:TEMP ("atlas-replay-comparison-" + [guid]::NewGuid().ToString("N") + ".json")
& .\.venv\Scripts\python.exe -m app.trade_intelligence.knowledge.replay_comparison `
    $Reports `
    --policy QUARTER_AT_075R_THEN_BREAK_EVEN `
    --policy QUARTER_AT_050R_THEN_BREAK_EVEN `
    --champion QUARTER_AT_075R_THEN_BREAK_EVEN `
    --output $Output
if ($LASTEXITCODE -ne 0) { throw "Replay comparison failed." }
Write-Host "COMPARISON_FILE=$Output"
```

Use `data/research/early_partial_v1/reports` for the older planned-entry
experiment in a separate invocation. Never combine those with the next-minute
entry-sizing reports. Existing output files are not overwritten. Inputs are
bounded to 500 files, 100 episodes per batch and 20 MB combined; larger research
collections need partitioned comparisons. Zero comparable episodes yields null
totals, not evidence of break-even performance. No paid feed or API key is used.
