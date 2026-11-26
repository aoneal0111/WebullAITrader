# One-command stopped/PAPER-only read-only evidence collection.
# Existing durable startup metrics and paper ledger only; no startup/order/network.
[CmdletBinding()]
param(
    [string] $Repo = (Join-Path $HOME 'WebullAITrader'),
    [string] $PerformanceDirectory = (Join-Path $env:TEMP 'atlas-performance')
)
$ErrorActionPreference = 'Stop'
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Py -PathType Leaf)) { throw 'ATLAS_VENV_NOT_FOUND' }
$Active = @(Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match '^(python|pythonw)\.exe$' -and
     $_.CommandLine -match 'app\.gui\.app|webull_ai_trader_launcher') -or
    $_.Name -match '^WebullAITrader\.exe$'
})
if ($Active.Count -gt 0) { throw 'STOP_ATLAS_BEFORE_AUDITING_LEDGER' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\paper-evidence-review-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Root -Force | Out-Null
Push-Location $Repo
try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $Snapshot = Get-ChildItem -LiteralPath $PerformanceDirectory -File -Filter '*.json' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if ($Snapshot) {
        $StartupOut = Join-Path $Root 'startup-health.json'
        & $Py -m app.strategy_research.atlas_startup_entry_health --snapshot $Snapshot.FullName --output $StartupOut
        if ($LASTEXITCODE -ne 0) { throw 'STARTUP_REPORT_FAILED' }
        $Data = Get-Content -LiteralPath $StartupOut -Raw | ConvertFrom-Json
        Write-Host "STARTUP_SOURCE=$($Snapshot.FullName)"
        Write-Host "GUI_SLA=$($Data.startup.gui_status)"
        Write-Host "FEED_SLA=$($Data.startup.status)"
        Write-Host "STARTUP_REPORT=$StartupOut"
    } else {
        Write-Host 'STARTUP_REPORT=NO_SAVED_SNAPSHOT'
    }
    # Only the configured PAPER ledger is permitted; this is no live broker call.
    $Ledger = (& $Py -c "from app.configuration import load_configuration; from pathlib import Path; c=load_configuration(); print(Path(c.execution_database_path).with_name('paper-execution.sqlite3') if c.environment.value=='PAPER' and not c.live_trading_enabled else 'NOT_PAPER')" | Select-Object -Last 1)
    if ($LASTEXITCODE -ne 0 -or $Ledger -eq 'NOT_PAPER') {
        Write-Host 'COST_REPORT=NOT_PAPER_OR_CONFIG_UNAVAILABLE'
    } elseif (Test-Path -LiteralPath $Ledger -PathType Leaf) {
        $Out = Join-Path $Root 'paper-execution-costs.json'
        $ErrorFile = Join-Path $Root 'paper-cost-audit.stderr.txt'
        & $Py -m app.strategy_research.execution_cost_audit --ledger $Ledger 1> $Out 2> $ErrorFile
        if ($LASTEXITCODE -eq 0) {
            $Cost = Get-Content -LiteralPath $Out -Raw | ConvertFrom-Json
            Write-Host "PAPER_MATCHED_FILLS=$($Cost.decision_reference_matches)"
            Write-Host "PAPER_UNMATCHED_FILLS=$($Cost.unmatched_fills)"
            Write-Host "MATCHED_ASK_REFERENCE_DELTA_PER_SHARE=$($Cost.matched_ask_anchor_difference_per_share)"
            Write-Host "MATCHED_COMMISSION_PER_SHARE=$($Cost.matched_recorded_commission_per_share)"
            Write-Host "COST_REPORT=$Out"
        } else {
            Write-Host "COST_REPORT=UNAVAILABLE_OR_SOURCE_VALIDATION_FAILED"
            Write-Host "COST_AUDIT_DIAGNOSTICS=$ErrorFile"
        }
    } else {
        Write-Host 'COST_REPORT=NO_CONFIGURED_PAPER_LEDGER'
    }
} finally {
    Pop-Location
}
Write-Host 'PAPER_EVIDENCE_REVIEW_FINISHED'
