# Analyze the most recent validated Atlas historical report. Read-only, no broker calls.
[CmdletBinding()]
param(
    [string] $Repo = (Join-Path $HOME 'WebullAITrader'),
    [string] $ResearchRoot = (Join-Path $env:LOCALAPPDATA 'AtlasResearch')
)
$ErrorActionPreference = 'Stop'
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Py -PathType Leaf)) {
    throw 'ATLAS_VENV_NOT_FOUND'
}
$Source = Get-ChildItem -LiteralPath $ResearchRoot -Directory -Filter 'validated-batch-*' |
    ForEach-Object {
        Get-Item -LiteralPath (Join-Path $_.FullName 'historical-micro-outcomes.json') -ErrorAction SilentlyContinue
    } | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
if ($null -eq $Source) { throw 'VALIDATED_HISTORICAL_REPORT_NOT_FOUND' }
$Out = Join-Path $Source.DirectoryName ('cost-sensitivity-' + [guid]::NewGuid().ToString('N') + '.json')
Push-Location $Repo
try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $Py -m app.strategy_research.warrior_micro_cost_sensitivity --source $Source.FullName --output $Out
    if ($LASTEXITCODE -ne 0) { throw 'COST_ANALYSIS_FAILED' }
} finally {
    Pop-Location
}
$Result = Get-Content -LiteralPath $Out -Raw | ConvertFrom-Json
Write-Host '=== COST SENSITIVITY SUMMARY ==='
Write-Host ("OBSERVED_RUNS=" + $Result.observed_trigger_runs)
Write-Host ("RESOLVED_CASES=" + $Result.resolved_cases)
Write-Host ("UNRESOLVED_CASES=" + $Result.unresolved_cases)
Write-Host ("RESOLVED_WIN_FRACTION=" + $Result.resolved_only_win_fraction)
Write-Host ("GROSS_RESOLVED_R=" + $Result.gross_resolved_only_r)
Write-Host ("BASELINE_NET_R=" + $Result.baseline_reconciled_net_r)
$Result.scenarios | Format-Table assumed_cost_per_side_per_share, resolved_only_net_r, profitable_resolved_only
Write-Host "AUDIT_FILE=$Out"
