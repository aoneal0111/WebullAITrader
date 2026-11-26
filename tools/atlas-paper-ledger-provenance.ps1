# Read-only bounded PAPER ledger provenance. No recursive searches or new ledgers.
[CmdletBinding()]
param(
    [string] $Repo = (Join-Path $HOME 'WebullAITrader'),
    [string] $TrialRoot = (Join-Path $env:LOCALAPPDATA 'AtlasResearch\atlas-paper-operation-68d85318b6354953b10c23029c2bb0a6')
)
$ErrorActionPreference = 'Stop'
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Py -PathType Leaf)) { throw 'ATLAS_VENV_NOT_FOUND' }
$Processes = @(Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match '^(python|pythonw)\.exe$' -and
     $_.CommandLine -match 'app\.gui\.app|webull_ai_trader_launcher') -or
    $_.Name -match '^WebullAITrader\.exe$'
})
if ($Processes.Count -gt 0) { throw 'STOP_ATLAS_BEFORE_READ_ONLY_INVENTORY' }
$OutRoot = Join-Path $env:LOCALAPPDATA ('AtlasResearch\ledger-provenance-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $OutRoot -Force | Out-Null
$Out = Join-Path $OutRoot 'ledger-inventory.json'
$CurrentLedger = Join-Path $Repo 'data\paper-execution.sqlite3'
$TrialLedger = Join-Path $TrialRoot 'paper\paper-execution.sqlite3'
$ResearchExperiment = Join-Path $TrialRoot 'review-source\data\paper_trade_experiment.sqlite3'
$Pointer = Join-Path $TrialRoot 'paper\recording-session.json'
Push-Location $Repo
try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $Py -m app.strategy_research.paper_ledger_provenance `
        --candidate $CurrentLedger `
        --candidate $TrialLedger `
        --candidate $ResearchExperiment `
        --recording-pointer $Pointer `
        --output $Out
    if ($LASTEXITCODE -ne 0) { throw 'PAPER_LEDGER_PROVENANCE_FAILED' }
    $Result = Get-Content -LiteralPath $Out -Raw | ConvertFrom-Json
    Write-Host '=== LEDGER PROVENANCE ==='
    $Result.candidates | Select-Object filename,classification,verified_paper_identity,order_count,campaign_count | Format-Table -AutoSize
    Write-Host "RECORDING_CLASSIFICATION=$($Result.recording.classification)"
    Write-Host "AUTHORITATIVE_LEDGER_PROVEN=$($Result.authoritative_ledger_identified)"
    Write-Host "INVENTORY_FILE=$Out"
} finally {
    Pop-Location
}
