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
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\warrior-library-repair-' + [guid]::NewGuid().ToString('N'))
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
    & $Python -m pytest tests/warrior_momentum/test_warrior_library_operation.py tests/opportunity_discovery tests/composition/test_taxonomy_paper_composition.py tests/warrior_momentum/test_forward_capture.py tests/strategies/test_autonomous_paper_bridge.py tests/asset_modules/test_continuous_paper.py tests/asset_modules/test_grok_warrior_control.py tests/asset_modules/test_grok_desktop_producer.py tests/asset_modules/test_paper_morning_status.py tests/warrior_momentum/test_quick_scalper_runtime.py tests/test_continuous_account_recovery.py tests/test_subscription_serialization.py tests/test_equity_network_recovery.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Warrior library, profit protection and recovery checks failed. Installed source unchanged.' }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM',$PreviousQt,'Process')
    Pop-Location
}
$Backup = 'backup/before-warrior-library-repair-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset attempted.' }
Push-Location $Repo
try {
    & $Python -m app.equity_overhaul.warrior_library_repair --paper $PaperDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Source updated but operating-spec migration failed. Preserve the error; do not launch Atlas.' }
} finally { Pop-Location }
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host "MANUAL_ATLAS=$Launcher"
Write-Host 'PREPARED_ONLY; NOTHING_STARTED; NO_API_REQUESTS; NO_ENV_FILE_CHANGED'
Write-Host 'EXISTING_PERSISTENT_OPERATION_ID_LAUNCHERS_SETTINGS_AND_LEDGER_UNCHANGED; CRYPTO_NOT_RESTARTED'
Write-Host 'WARRIOR_LIBRARY=23_IMPLEMENTED_OF_30_CATALOGUED; ORIGINAL_STRATEGY_NAMES_RETAINED; GROK_REVIEW_RETAINED'
Write-Host 'WARRIOR_HARVEST=MARKET_PARTIAL_WITH_RUNNER_STOP; SCALPER_PARAMETERS_UNCHANGED; NO_GUARANTEED_PROFIT'
Write-Host 'EXTERNAL_WEBULL_CONNECTIVITY_NOT_VERIFIED; AUTHENTICATION_AND_NONRETRYABLE_FAILURES_STILL_TERMINATE'
