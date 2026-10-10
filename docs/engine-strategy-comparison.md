# Offline strategy comparison

`app.asset_modules.strategy_comparison` summarizes caller-supplied results for
frozen policies, separately for each of the five engines. It does not run a
strategy, fetch data, start workers/Atlas, validate fills, submit orders, call AI
or promote policies. This is the comparison layer for subsequent replay adapters.

Use one experiment, dataset and cost-model identity per input. Do not combine
actual paper fills, quote proxies and minute-bar proxies. Those evidence types
have different meanings and must be separate reports. Each policy version must
represent fixed rules; changing parameters requires a new version/experiment.

Every result declares engine, episode, instrument, trading date, frozen policy,
status and net PnL. CLOSED amounts must be finite decimal strings with all
declared costs already included. UNRESOLVED and NO_ENTRY require null PnL.
Missing rows are reported as MISSING rather than silently counted as zero.
Duplicate episode/policy identities and mismatched episode context are refused.

Totals use only episodes CLOSED for every declared policy of that engine. This
supports exit comparisons using identical entries. It is not a fair standalone
assessment of entry policies that trade different opportunities: their unpaired
results need a separate opportunity/portfolio evaluation. The output exposes
exclusions and symbol/date counts; repeated episodes are not independent samples.
Zero paired data produces null totals, not a zero-profit finding. Profit factor
is null with no paired losses rather than an infinite profitability claim.

No portfolio drawdown, shared-capital simulation, uncertainty interval or winner
is produced. Episode totals cannot establish account returns or transferable
profitability. Sparse capture may preferentially exclude difficult trades.

## Minimal input

Save as a UTF-8 JSON file (PowerShell UTF-8 BOM is accepted):

```json
{
  "version": "ENGINE_COMPARISON_V1",
  "experiment_id": "warrior-exits-v1",
  "dataset_id": "immutable-capture-id",
  "cost_model_id": "declared-fees-and-execution-v1",
  "evidence_kind": "QUOTE_PROXY",
  "engines": {
    "WARRIOR_MOMENTUM_V1": {
      "champion": "current-exit-v1",
      "policies": ["current-exit-v1", "peak-defense-v1"]
    }
  },
  "results": []
}
```

Result row shape:

```json
{
  "engine": "WARRIOR_MOMENTUM_V1", "policy": "current-exit-v1",
  "episode_id": "capture-lifecycle-id", "instrument": "NAUT",
  "trading_date": "2026-10-09", "status": "CLOSED", "net_pnl": "28.85"
}
```

An adapter must construct these rows from its actual evidence. Do not label a
counterfactual exit as ACTUAL_PAPER_FILLS. Dates use the instrument's trading
calendar, not the workstation's arbitrary local date. Options use contract
identities; futures use expiry-specific contracts. Cost/multiplier accounting
belongs in those adapters, not this summary module.

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/asset_modules/test_strategy_comparison.py -q
if ($LASTEXITCODE -ne 0) { throw "Comparison tests failed." }
& .\.venv\Scripts\python.exe -m app.asset_modules.strategy_comparison .\comparison-input.json
if ($LASTEXITCODE -ne 0) { throw "Comparison failed." }
```

Inputs are bounded at 20 MB, 100000 rows and 20 policies per engine. Keep reports
outside the execution hot path. Integration with captured episodes is the next
step; this module alone does not make the five research strategies operational.
