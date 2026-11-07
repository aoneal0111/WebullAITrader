param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|bootstrap-observer\.py'
}
if ($Running) { throw 'Close Atlas normally before updating. Nothing was changed.' }
$Dirty = & git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0) { throw 'Repository check failed.' }
if ($Dirty) { throw 'Tracked local changes exist. Nothing was overwritten.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not on the research branch.' }
& git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Your branch needs integration. Nothing was changed.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\daily-recording-install-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
Push-Location $Source
try {
    $Checks = @(
        'tests/equity_overhaul/test_cent_portfolio.py'
        'tests/equity_overhaul/test_cent_scalper.py'
        'tests/warrior_momentum/test_desktop_sidecar.py'
        'tests/asset_modules/test_daily_research.py'
        'tests/asset_modules/test_daily_observer_protocol.py'
        'tests/asset_modules/test_grok_observer_capture.py'
        'tests/asset_modules/test_grok_observer_runtime_tap.py'
        'tests/asset_modules/test_grok_observer_session.py'
        'tests/asset_modules/test_grok_background_observer.py'
        'tests/asset_modules/test_grok_online_observer.py'
        'tests/composition/test_desktop_composition.py'
        'tests/equity_overhaul/test_admission.py'
        'tests/warrior_momentum/test_execution_quote_confirmation.py'
        'tests/test_desktop_broker_runtime.py'
        'tests/test_desktop_runtime_bootstrap_composition.py'
        'tests/test_broker_runtime_factory.py'
    )
    & $Python -m pytest @Checks --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Windows checks failed. Installed source and settings unchanged.' }
}
finally { Pop-Location }
$Backup = 'backup/before-daily-recording-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup creation failed.' }
Write-Host "BACKUP_BRANCH=$Backup"
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset was performed.' }
Push-Location $Repo
try {
    & $Python -m app.asset_modules.daily_research_setup --repo $Repo
    if ($LASTEXITCODE -ne 0) { throw 'Recording opt-in failed; source installed but no bot started. Send the error.' }
    & $Python -m app.asset_modules.research_history_inventory --repo $Repo --output (Join-Path $Root 'history-inventory.json')
    if ($LASTEXITCODE -ne 0) { throw 'History indexing failed. Recording setup was saved; Atlas was not started.' }
}
finally { Pop-Location }
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host 'OPEN_ATLAS_NORMALLY; CLICK_START_NORMALLY; RECORDING_IN_BACKGROUND'
Write-Host 'NO_TRADING_PERMISSIONS_CHANGED; NO_AUTOMATIC_GROK_REQUESTS'
