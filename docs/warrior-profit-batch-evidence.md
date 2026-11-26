# Warrior profit retention — broader closed-trade evidence

## Purpose

The October 9 NAUT/CCI capture contained only **three reconciled
lifecycles**. A single losing trade emitting a 0.40R/50% giveback signal
cannot establish a profitable exit policy.

The batch analyzer compares all *explicitly supplied* closed Warrior
captures by actual realized result (winner, loser, flat) and counts only
**observed chronological drawdown signals**. It never replaces recorded
fills or claims that a signal was executable. Its fixed 10-scenario grid
reuses the existing capture reconciler.

## Existing offline exporter

Atlas already ships the read-only module
`app.trade_intelligence.knowledge.capture_profit_export`. It reads
only the current PAPER campaign, limits exports to five chronological
closed lifecycles per invocation, and saves `batch_N_capture.json`
plus a manifest in a new directory and ZIP. It does not query brokers.

A timestamped `snapshot.json` with `runtime_started_at` is required
by the exporter. Do not invent a snapshot or set a start time to cherry-pick
winners or losers. Check the manifest's `eligible_closed`, `exports`
and `excluded` before deciding whether the capture set is representative.

## Running the new analysis

From an isolated Windows research worktree, using Atlas's existing
Python 3.13 interpreter:

```powershell
$Batch = @(
    Get-ChildItem -LiteralPath "C:\path\to\exported\captures" -Filter "batch_*_capture.json" -File |
    Sort-Object Name |
    Select-Object -First 20 -ExpandProperty FullName
)
if ($Batch.Count -eq 0) { throw "No explicit capture inputs" }
& $Python -m app.trade_intelligence.knowledge.capture_profit_batch @Batch |
    Set-Content -LiteralPath "warrior-profit-batch.json" -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "Offline profit batch analysis failed" }
```

This command reads only explicitly selected capture JSON files, not
manifests, existing matrix outputs, SQLite files or credentials. It
requires 1–20 inputs (each no larger than 8 MB and 20 MB total).

## Interpretation

For each activation/retention setting, output includes counts of
`winning_trade_signals`, `losing_trade_signals` and
`armed_without_signal`; `actual_net_pnl` remains exactly the net of
actual reconciled historical fills and commissions, regardless of
threshold. `hypothetical_net_pnl` and
`unknown_execution_fill_count` are always null.

Unreconciled captures are excluded from all outcome and signal
numerators, but counted under `excluded_unreconciled`. Repeated
campaign/lifecycle identities across files are rejected instead of
double-counted.

Avoid selecting the most aggressive threshold by number of signals.
After a broad chronological retrospective, perform out-of-sample
validation with independently sampled winners and losers, realistic
execution liquidity, position protection and restart tests, then
controlled forward PAPER sessions before changing trade management.

No runtime policy, gateway, market-data source, stop threshold, live
trading setting, or installed workstation is changed by this tool.
