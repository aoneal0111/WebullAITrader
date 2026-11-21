param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$PaperDirectory
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$PaperDirectory = (Resolve-Path -LiteralPath $PaperDirectory).Path
$Launcher = Join-Path $PaperDirectory 'Start-Atlas-Paper.ps1'
if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) { throw 'Existing continuous PAPER launcher missing.' }
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|grok_day_proposals|bootstrap-observer\.py'
}
if ($Running) { throw 'Close Atlas and Grok observers normally before updating equity.' }
$Dirty = & git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Tracked changes detected. Nothing overwritten.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
& git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Repository requires integration. Nothing reset.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\shared-market-workspace-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
$PreviousQt = [Environment]::GetEnvironmentVariable('QT_QPA_PLATFORM','Process')
Push-Location $Source
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    & $Python -c "import sys; from pathlib import Path; from app.equity_overhaul.paper_experiment import load, CONTINUOUS_SCOPE; assert load(Path(sys.argv[1]) / 'experiment.json', allow_expired=True)[1]['scope'] == CONTINUOUS_SCOPE" $PaperDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Existing continuous PAPER operating spec required. Nothing changed.' }
    $CheckRunner = Join-Path $Root 'run-gui-checks.py'
    @'
import sys
from pathlib import Path
from PySide6.QtCore import QSettings
sys.path.insert(0, sys.argv[1])
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(Path(sys.argv[2]) / 'qt-settings'))
if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main(['tests/gui', 'tests/asset_modules/test_desktop_markets.py',
        'tests/asset_modules/test_market_analysis.py', 'tests/asset_modules/test_futures_ledger_view.py',
        'tests/asset_modules/test_options_ledger_view.py', 'tests/strategy_lab/test_crypto_monitor.py',
        '-q', '--basetemp', str(Path(sys.argv[2]) / 'checks')]))
'@ | Set-Content -LiteralPath $CheckRunner -Encoding UTF8
    & $Python $CheckRunner $Source $Root
    if ($LASTEXITCODE -ne 0) { throw 'Shared market layout regression checks failed. Installed source unchanged.' }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM',$PreviousQt,'Process')
    Pop-Location
}
$SpecBefore = (Get-FileHash -LiteralPath (Join-Path $PaperDirectory 'experiment.json') -Algorithm SHA256).Hash
$Backup = 'backup/before-shared-market-workspace-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset attempted.' }
if ((Get-FileHash -LiteralPath (Join-Path $PaperDirectory 'experiment.json') -Algorithm SHA256).Hash -ne $SpecBefore) {
    throw 'Operating spec changed outside the GUI update. Preserve this diagnostic.'
}
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host "MANUAL_ATLAS=$Launcher"
Write-Host 'PREPARED_ONLY; NOTHING_STARTED; NO_API_REQUESTS; NO_ENV_FILE_CHANGED'
Write-Host 'EXISTING_PERSISTENT_OPERATION_ID_LAUNCHERS_SETTINGS_AND_LEDGER_UNCHANGED; CRYPTO_NOT_RESTARTED'
Write-Host 'SHARED_LAYOUT=EQUITY_CRYPTO_FUTURES_OPTIONS; ACCOUNT_RISK_VISIBLE_ON_ALL_ROUTES'
Write-Host 'CRYPTO_WALLETS_SEPARATE; UNKNOWN_BALANCES_NOT_INFERRED; FUTURES_OPTIONS_AVAILABILITY_RETAINED'
Write-Host 'GUI_ONLY; STRATEGY_PARAMETERS_GROK_POLICY_AND_OPERATING_SPEC_UNCHANGED'
Write-Host 'EXTERNAL_WEBULL_CONNECTIVITY_NOT_VERIFIED; AUTHENTICATION_AND_NONRETRYABLE_FAILURES_STILL_TERMINATE'
