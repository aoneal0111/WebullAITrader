# Read-only closed recording event-type inspection. No startup or recursive search.
[CmdletBinding()]
param(
    [string] $Repo = (Join-Path $HOME 'WebullAITrader'),
    [string] $TrialRoot = (Join-Path $env:LOCALAPPDATA 'AtlasResearch\atlas-paper-operation-68d85318b6354953b10c23029c2bb0a6')
)
$ErrorActionPreference = 'Stop'
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Py -PathType Leaf)) { throw 'ATLAS_VENV_NOT_FOUND' }
$AtlasProcesses = @(Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match '^(python|pythonw)\.exe$' -and
     $_.CommandLine -match 'app\.gui\.app|webull_ai_trader_launcher') -or
    $_.Name -match '^WebullAITrader\.exe$'
})
if ($AtlasProcesses.Count -gt 0) { throw 'STOP_ATLAS_BEFORE_CLOSED_RECORDING_REVIEW' }
$OutRoot = Join-Path $env:LOCALAPPDATA ('AtlasResearch\closed-recording-review-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $OutRoot -Force | Out-Null
$Out = Join-Path $OutRoot 'event-shape-review.json'
$Pointer = Join-Path $TrialRoot 'paper\recording-session.json'
Push-Location $Repo
try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $Py -m app.strategy_research.closed_recording_evidence --recording-pointer $Pointer --output $Out
    if ($LASTEXITCODE -ne 0) { throw 'CLOSED_RECORDING_REVIEW_FAILED' }
    $Data = Get-Content -LiteralPath $Out -Raw | ConvertFrom-Json
    Write-Host "EVENT_FILES=$($Data.file_count)"
    Write-Host "EVENT_LINES=$($Data.line_count)"
    Write-Host "INVALID_LINES=$($Data.invalid_lines)"
    Write-Host "UNKNOWN_EVENT_LINES=$($Data.unknown_event_lines)"
    Write-Host '=== CANDIDATE EVENT TYPES (UNVERIFIED) ==='
    $Data.candidate_type_counts.PSObject.Properties |
        ForEach-Object { Write-Host ($_.Name + '=' + $_.Value) }
    Write-Host "FILLS_RECONCILED=$($Data.fills_reconciled)"
    Write-Host "PROTECTIVE_EXITS_VERIFIED=$($Data.protective_exits_verified)"
    Write-Host "REPORT=$Out"
} finally {
    Pop-Location
}
