param(
    [Parameter(Mandatory=$true)][ValidateSet('ON','OFF')][string]$Mode,
    [Parameter(Mandatory=$true)][string]$PaperDirectory,
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader')
)
$ErrorActionPreference = 'Stop'
$PaperDirectory = (Resolve-Path -LiteralPath $PaperDirectory).Path
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$SpecPath = Join-Path $PaperDirectory 'experiment.json'
$Launcher = Join-Path $PaperDirectory 'Start-Atlas-Paper.ps1'
if (-not (Test-Path -LiteralPath $SpecPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    throw 'Existing continuous PAPER operation and launcher required. No changes.'
}
$Spec = Get-Content -LiteralPath $SpecPath -Raw | ConvertFrom-Json
if ($Spec.scope -ne 'NATIVE_PAPER_OPERATION_V1' -or
    $Spec.profile -notin @('GROK_WARRIOR_V5','GROK_WARRIOR_ACCOUNT_V6')) {
    throw 'This switch requires the dedicated continuous Grok Warrior PAPER profile. No changes.'
}
$Running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and
    $_.CommandLine -match 'app\.gui\.app|grok_day_proposals|bootstrap-observer\.py'
})
if ($Running.Count -gt 0) { throw 'Close Atlas and Grok observers normally before switching modes. No changes.' }
$Marker = Join-Path $PaperDirectory 'STOP_GROK'
if ($Mode -eq 'OFF') {
    if (-not (Test-Path -LiteralPath $Marker)) {
        [System.IO.File]::WriteAllText($Marker, 'Grok disabled for next Atlas startup.' + [Environment]::NewLine)
    }
    Write-Host 'GROK_MODE=OFF'
    Write-Host 'NATIVE_WARRIOR_REVIEW_BINDING=NOT_INSTALLED_ON_NEXT_START'
} else {
    if (Test-Path -LiteralPath $Marker) {
        Remove-Item -LiteralPath $Marker -Force
    }
    Write-Host 'GROK_MODE=ON'
    Write-Host 'GROK_REVIEW_BINDING=ENABLED_ON_NEXT_START_IF_CONFIGURATION_VALID'
}
Write-Host 'ATLAS_NOT_STARTED; EXISTING_PAPER_LEDGER_AND_PROTECTION_NOT_MODIFIED'
Write-Host 'RESTART_REQUIRED; PENDING_BROKER_ACKNOWLEDGEMENTS_REQUIRE_NORMAL_RECONCILIATION'
Write-Host ('MANUAL_LAUNCHER=' + $Launcher)
