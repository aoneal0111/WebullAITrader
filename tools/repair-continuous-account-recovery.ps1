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
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\continuous-account-recovery-' + [guid]::NewGuid().ToString('N'))
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
    & $Python -m pytest tests/test_continuous_account_recovery.py tests/asset_modules/test_continuous_paper.py tests/test_subscription_serialization.py tests/test_equity_network_recovery.py tests/test_stale_feed_watchdog.py tests/test_live_scanner.py tests/test_webull_stream_session_lifecycle.py tests/test_webull_stream_transport.py tests/test_phase5_3_startup_lifecycle.py tests/test_desktop_broker_runtime.py tests/test_desktop_market_data_streaming.py tests/test_p2_live_failure_containment.py tests/asset_modules/test_paper_morning_status.py tests/warrior_momentum/test_partial_fill_projection_lag.py tests/warrior_momentum/test_quick_scalper_runtime.py tests/asset_modules/test_grok_warrior_control.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Stream/account recovery regression checks failed. Installed source unchanged.' }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM',$PreviousQt,'Process')
    Pop-Location
}
$Backup = 'backup/before-continuous-account-recovery-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset attempted.' }
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host "MANUAL_ATLAS=$Launcher"
Write-Host 'PREPARED_ONLY; NOTHING_STARTED; NO_API_REQUESTS; NO_ENV_FILE_CHANGED'
Write-Host 'EXISTING_PERSISTENT_OPERATION_ID_LAUNCHERS_SETTINGS_AND_LEDGER_UNCHANGED; CRYPTO_NOT_RESTARTED'
Write-Host 'CONTINUOUS_PAPER_TRANSIENT_ACCOUNT_READS_RETRY_WITH_BACKOFF; NEW_ENTRIES_PAUSE_UNTIL_REAL_ACCOUNT_SNAPSHOT_IS_PUBLISHED'
Write-Host 'EXTERNAL_WEBULL_CONNECTIVITY_NOT_VERIFIED; AUTHENTICATION_AND_NONRETRYABLE_FAILURES_STILL_TERMINATE'
