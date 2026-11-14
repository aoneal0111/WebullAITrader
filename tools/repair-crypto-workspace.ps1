param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$PaperDirectory,
    [switch]$ApplyWorkers,
    [string]$RecoverPlan
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$PaperDirectory = (Resolve-Path -LiteralPath $PaperDirectory).Path
if (-not (Test-Path -LiteralPath (Join-Path $PaperDirectory 'Start-Atlas-Paper.ps1') -PathType Leaf)) { throw 'Existing continuous PAPER launcher missing.' }
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|grok_day_proposals|bootstrap-observer\.py'
}
if ($Running) { throw 'Close Atlas normally before installing. Crypto workers can remain running for preparation.' }
$Dirty = & git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Tracked changes detected. Nothing overwritten.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
& git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Repository requires integration. Nothing reset.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\crypto-workspace-repair-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
$PreviousQt = [Environment]::GetEnvironmentVariable('QT_QPA_PLATFORM','Process')
Push-Location $Source
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    $Runner = Join-Path $Root 'checks.py'
    @'
import sys
from pathlib import Path
from PySide6.QtCore import QSettings
sys.path.insert(0,sys.argv[1])
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,str(Path(sys.argv[2])/'qt-settings'))
if __name__=='__main__':
    import pytest
    raise SystemExit(pytest.main(['tests/gui','tests/strategy_lab/test_crypto_monitor.py',
        'tests/strategy_lab/test_crypto_workspace_control.py','tests/strategy_lab/test_crypto_workspace_telemetry.py',
        'tests/strategy_lab/test_crypto_fleet.py','tests/strategy_lab/test_nfi_forward_paper.py',
        '-q','--basetemp',str(Path(sys.argv[2])/'checks')]))
'@ | Set-Content -LiteralPath $Runner -Encoding UTF8
    & $Python $Runner $Source $Root
    if ($LASTEXITCODE -ne 0) { throw 'Crypto workspace checks failed. Installed source unchanged.' }
    $Overlays = Join-Path $Root 'worker-overlays'
    $PrepareArgs = @('--output', $Overlays)
    if ($RecoverPlan) {
        $RecoverPlan = (Resolve-Path -LiteralPath $RecoverPlan).Path
        & $Python -m app.strategy_lab.crypto_workspace_upgrade --output $Overlays --restore-original --recover-plan $RecoverPlan
        if ($LASTEXITCODE -ne 0) { throw 'Original worker recovery failed. Preserve output and inspect Docker state.' }
        $PrepareArgs += @('--recover-plan', $RecoverPlan)
    }
    & $Python -m app.strategy_lab.crypto_workspace_upgrade @PrepareArgs
    if ($LASTEXITCODE -ne 0) { throw 'Worker overlay preparation failed. No new telemetry applied; any original recovery is reported above.' }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM',$PreviousQt,'Process')
    Pop-Location
}
$Spec = Join-Path $PaperDirectory 'experiment.json'
$SpecBefore = (Get-FileHash -LiteralPath $Spec -Algorithm SHA256).Hash
$Backup = 'backup/before-crypto-workspace-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed.' }
if ((Get-FileHash -LiteralPath $Spec -Algorithm SHA256).Hash -ne $SpecBefore) { throw 'Operating spec unexpectedly changed.' }
$Apply = Join-Path $Root 'Apply-Crypto-Telemetry.ps1'
$Body = @'
$ErrorActionPreference='Stop'
Push-Location '__SOURCE__'
try {
    & '__PYTHON__' -m app.strategy_lab.crypto_workspace_upgrade --output '__OVERLAYS__' --apply
    if ($LASTEXITCODE -ne 0) { throw 'Worker update failed. Preserve output; inspect workers before continuing.' }
} finally { Pop-Location }
'@
$Body.Replace('__SOURCE__',$Source.Replace("'","''")).Replace('__PYTHON__',$Python.Replace("'","''")).Replace('__OVERLAYS__',$Overlays.Replace("'","''")) | Set-Content -LiteralPath $Apply -Encoding UTF8
Write-Host "REPAIR_DIRECTORY=$Root"
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host "APPLY_WORKER_TELEMETRY=$Apply"
Write-Host "MANUAL_ATLAS=$(Join-Path $PaperDirectory 'Start-Atlas-Paper.ps1')"
if ($ApplyWorkers) {
    Write-Host 'APPLYING_TELEMETRY; EXISTING_CRYPTO_WORKERS_RECREATED_WITH_SAME_WALLETS_AND_STRATEGIES; PREVIOUS_RUNNING_STATE_WILL_BE_VERIFIED'
    & $Apply
} else { Write-Host 'GUI_INSTALLED; WORKER_OVERLAYS_PREPARED_ONLY; RUN_APPLY_WORKER_TELEMETRY_TO_CONNECT_NATIVE_VALUES' }
Write-Host 'KEEP_REPAIR_DIRECTORY; WORKER_TELEMETRY_BINDS_REFERENCE_ITS_FILES'
Write-Host 'LIVE_OFF; EQUITY_NOT_STARTED; STRATEGY_PARAMETERS_AND_LEDGERS_PRESERVED'
Write-Host 'CRYPTO_STOP_RETAINS_HOLDINGS_AND_STOPS_EXIT_MANAGEMENT; START_RESUMES_SAVED_WALLET; STOP_ALL_CRYPTO_IS_CRYPTO_ONLY'
