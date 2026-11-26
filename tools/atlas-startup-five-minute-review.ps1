# Offline five-minute Atlas startup audit against an existing performance snapshot.
# No runtime start, broker call, parameter update, or network access.
[CmdletBinding()]
param(
    [string] $Repo = (Join-Path $HOME 'WebullAITrader'),
    [string] $Snapshot,
    [string] $OutputDirectory = (Join-Path $env:LOCALAPPDATA 'AtlasResearch\startup-health-reviews')
)
$ErrorActionPreference = 'Stop'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'ATLAS_VENV_NOT_FOUND'
}
if (-not $Snapshot) {
    $Diagnostics = Join-Path $env:TEMP 'atlas-performance'
    $Latest = Get-ChildItem -LiteralPath $Diagnostics -File -Filter '*.json' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($null -eq $Latest) { throw 'NO_EXISTING_PERFORMANCE_SNAPSHOT' }
    $Snapshot = $Latest.FullName
}
if (-not (Test-Path -LiteralPath $Snapshot -PathType Leaf)) {
    throw 'PERFORMANCE_SNAPSHOT_MISSING'
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$Output = Join-Path $OutputDirectory ('atlas-startup-audit-' + [guid]::NewGuid().ToString('N') + '.json')
Push-Location $Repo
try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $Python -m app.strategy_research.atlas_startup_entry_health --snapshot "$Snapshot" --output "$Output"
    if ($LASTEXITCODE -ne 0) { throw 'STARTUP_AUDIT_FAILED' }
} finally {
    Pop-Location
}
Write-Host "UPLOAD_FILE=$Output"
