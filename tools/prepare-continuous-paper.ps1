param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.(app|crypto_monitor)'
}
if ($Running) { throw 'Close the Atlas and monitor windows normally before updating. Leave crypto containers running.' }
$Dirty = & git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Tracked changes detected. Nothing overwritten.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
& git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Repository requires integration. Nothing reset.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\atlas-paper-operation-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
$PreviousQt = [Environment]::GetEnvironmentVariable('QT_QPA_PLATFORM','Process')
Push-Location $Source
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    & $Python -m pytest tests/asset_modules/test_continuous_paper.py tests/asset_modules/test_daily_research.py tests/asset_modules/test_grok_engine_teams.py tests/asset_modules/test_grok.py tests/asset_modules/test_grok_rate_limit.py tests/asset_modules/test_paper_morning_status.py tests/equity_overhaul/test_account_paper_sizing.py tests/strategy_lab/test_crypto_monitor.py tests/gui/test_production_main_window_layout.py tests/test_subscription_serialization.py tests/test_equity_network_recovery.py tests/test_stale_feed_watchdog.py tests/warrior_momentum/test_triggered_stop_progress.py tests/warrior_momentum/test_partial_fill_projection_lag.py tests/asset_modules/test_grok_warrior_control.py tests/asset_modules/test_paper_day_setup.py tests/equity_overhaul/test_expired_trial_protection.py tests/asset_modules/test_desktop_markets.py tests/gui/test_dashboard_shell.py tests/test_shutdown_provenance.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Continuous PAPER regression checks failed. Installed source unchanged.' }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM',$PreviousQt,'Process')
    Pop-Location
}
$Backup = 'backup/before-atlas-paper-operation-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset attempted.' }
Push-Location $Repo
try {
    & $Python -m app.asset_modules.paper_day_setup --repo $Repo --output (Join-Path $Root 'paper') --profile GROK_WARRIOR_ACCOUNT_V6 --extended-sessions --clock-tolerance --hours 24 --continuous
    if ($LASTEXITCODE -ne 0) { throw 'Persistent PAPER preparation failed. Nothing started.' }
} finally { Pop-Location }
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host 'CRYPTO_TAB_INTEGRATED; NOTHING_STARTED; EQUITY_LEFT_STOPPED; CRYPTO_CONTAINERS_UNCHANGED'
Write-Host 'CONTINUOUS_PAPER; NO_EXPIRY_OR_PAID_CALL_QUOTA; GROK_EMBEDDED_WARRIOR_ONLY; SCALPER_NATIVE'
Write-Host 'OPEN_ATLAS_WITH_MANUAL_ATLAS; CRYPTO_TAB_SHOWS_EXISTING_DOCKER_PAPER_WALLETS'
Write-Host 'NO_PROVIDER_OR_GROK_REQUESTS; NO_ORDERS; EXISTING_WALLETS_SETTINGS_AND_OLD_TRIALS_UNCHANGED; NEW_PERSISTENT_OPERATION_IS_EXPLICIT'
