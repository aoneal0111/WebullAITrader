param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Trial
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Trial = (Resolve-Path -LiteralPath $Trial).Path
if (-not (Test-Path -LiteralPath (Join-Path $Trial "experiment.json"))) { throw "Existing trial specification missing." }
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|grok_day_proposals|bootstrap-observer\.py'
}
if ($Running) { throw 'Close Atlas and old Grok observers normally before repairing equity. Crypto containers need no restart.' }
$Dirty = & git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Tracked changes detected. Nothing overwritten.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
& git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Branch integration required. Nothing reset.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\equity-session-repair-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
$BeforeQt = [Environment]::GetEnvironmentVariable('QT_QPA_PLATFORM','Process')
Push-Location $Source
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    & $Python -m pytest tests/asset_modules/test_paper_day_setup.py tests/asset_modules/test_paper_morning_status.py tests/asset_modules/test_grok_observer_runtime_tap.py tests/asset_modules/test_grok_observer_capture.py tests/asset_modules/test_grok_input_health.py tests/asset_modules/test_grok_day_proposals.py tests/warrior_momentum/test_partial_fill_projection_lag.py tests/warrior_momentum/test_paper_stream_quote.py tests/warrior_momentum/test_quick_scalper_runtime.py tests/strategies/test_owned_paper_quantity.py tests/strategies/test_autonomous_paper_bridge.py tests/asset_modules/test_controlled_paper_service.py tests/asset_modules/test_admission_controller.py tests/asset_modules/test_grok_rate_limit.py tests/asset_modules/test_grok.py tests/asset_modules/test_grok_failure.py tests/asset_modules/test_grok_warrior_control.py tests/equity_overhaul/test_expired_trial_protection.py tests/paper_gateway tests/composition/test_desktop_composition.py tests/asset_modules/test_daily_research.py tests/asset_modules/test_equity_policy_attribution.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Management/quote/controller regression checks failed. Installed source unchanged.' }
    & $Python -m app.asset_modules.paper_morning_status --trial $Trial
    if ($LASTEXITCODE -ne 0) { throw 'Existing trial read-only check failed. Source and ledger unchanged.' }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM',$BeforeQt,'Process')
    Pop-Location
}
$Backup = 'backup/before-equity-session-repair-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset attempted.' }
Push-Location $Repo
try {
    & $Python -m app.asset_modules.paper_day_setup --repo $Repo --output (Join-Path $Root 'trial') --profile GROK_WARRIOR_ACCOUNT_V6 --extended-sessions --clock-tolerance --hours 24
    if ($LASTEXITCODE -ne 0) { throw 'Source installed; fresh manual trial preparation failed. Nothing started.' }
} finally { Pop-Location }
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host 'REPAIR_INSTALLED; ATLAS_LEFT_STOPPED; CRYPTO_NOT_RESTARTED; NO_API_REQUESTS'
Write-Host 'EXISTING_LEDGER_AND_POSITION_PROTECTION_UNCHANGED; MANUAL_START_REQUIRED'
Write-Host 'EXISTING_TRIALS_BUDGETS_DEADLINES_AND_LEDGERS_RETAINED; EXPIRED_TRIAL_STARTUP_ALLOWS_PROTECTION_ONLY'
Write-Host 'NEW_TRIAL_IS_PREPARED_ONLY; ITS_24_HOUR_CLOCK_STARTS_AT_MANUAL_LAUNCH; WARRIOR_MAX_20_PAID_CALLS; DIRECTOR_8_SLOTS_3_HOURS_APART'
