param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Evidence
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Evidence = (Resolve-Path -LiteralPath $Evidence).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|grok_day_proposals|bootstrap-observer\.py'
}
if ($Running) { throw 'Close Atlas and old Grok observers normally before preparing the equity timing trial. Crypto containers can remain running.' }
$Dirty = & git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Tracked changes detected. Nothing overwritten.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
& git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Branch integration required. Nothing reset.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\equity-clock-trial-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
$BeforeQt = [Environment]::GetEnvironmentVariable('QT_QPA_PLATFORM','Process')
Push-Location $Source
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    & $Python -m pytest tests/warrior_momentum/test_paper_clock_tolerance.py tests/asset_modules/test_scalper_clock_replay.py tests/warrior_momentum/test_quick_scalper.py tests/warrior_momentum/test_quick_scalper_runtime.py tests/warrior_momentum/test_cent_stream_support.py tests/warrior_momentum/test_cent_executable_v3.py tests/warrior_momentum/test_cent_defense_floor.py tests/asset_modules/test_grok_warrior_control.py tests/equity_overhaul/test_account_paper_sizing.py tests/asset_modules/test_paper_day_setup.py tests/asset_modules/test_scalper_repair_replay.py tests/asset_modules/test_scalper_evidence_audit.py tests/paper_gateway/test_stale_entry_invalidation.py tests/test_paper_execution_engine.py tests/test_paper_matching_engine.py tests/asset_modules/test_paper_morning_status.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Timing/execution regression checks failed. Installed source unchanged.' }
    & $Python -m app.asset_modules.scalper_clock_replay --evidence $Evidence --output (Join-Path $Root 'clock-comparison.json')
    if ($LASTEXITCODE -ne 0) { throw 'Recorded baseline comparison failed. Installed source unchanged.' }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM',$BeforeQt,'Process')
    Pop-Location
}
$Backup = 'backup/before-paper-clock-trial-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset attempted.' }
Push-Location $Repo
try {
    & $Python -m app.asset_modules.paper_day_setup --repo $Repo --output (Join-Path $Root 'trial') --profile GROK_WARRIOR_ACCOUNT_V6 --extended-sessions --clock-tolerance
    if ($LASTEXITCODE -ne 0) { throw 'Source installed; manual trial preparation failed. Nothing started.' }
} finally { Pop-Location }
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host 'PREPARED_ONLY; NOTHING_STARTED; NO_API_REQUESTS; LIVE_OFF; CRYPTO_NOT_RESTARTED'
Write-Host 'NEW_8_HOUR_TRIAL_BEGINS_AT_MANUAL_LAUNCH; OLD_TRIALS_BUDGETS_AND_LEDGER_PRESERVED'
Write-Host 'SCALPER_CLOCK_TOLERANCE=250MS; BOOK_AGE_LIMIT=5S; ORIGINAL_PROVIDER_TIMES_PRESERVED; STOPS_UNCHANGED'
