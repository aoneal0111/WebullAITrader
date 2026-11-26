param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Review
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Review = (Resolve-Path -LiteralPath $Review).Path
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
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\equity-management-repair-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
$BeforeQt = [Environment]::GetEnvironmentVariable('QT_QPA_PLATFORM','Process')
Push-Location $Source
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    & $Python -m pytest tests/warrior_momentum/test_partial_fill_projection_lag.py tests/warrior_momentum/test_paper_stream_quote.py tests/warrior_momentum/test_quick_scalper_runtime.py tests/strategies/test_owned_paper_quantity.py tests/strategies/test_autonomous_paper_bridge.py tests/asset_modules/test_controlled_paper_service.py tests/asset_modules/test_admission_controller.py tests/asset_modules/test_grok_rate_limit.py tests/asset_modules/test_grok.py tests/asset_modules/test_grok_failure.py tests/asset_modules/test_grok_warrior_control.py tests/equity_overhaul/test_expired_trial_protection.py tests/paper_gateway tests/composition/test_desktop_composition.py tests/asset_modules/test_daily_research.py tests/asset_modules/test_equity_policy_attribution.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Management/quote/controller regression checks failed. Installed source unchanged.' }
    & $Python -m app.asset_modules.equity_recovery_check --review $Review --output (Join-Path $Root 'offline-recovery')
    if ($LASTEXITCODE -ne 0) { throw 'Private-copy recovery check failed. Installed source and production ledger unchanged.' }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM',$BeforeQt,'Process')
    Pop-Location
}
$Backup = 'backup/before-equity-management-repair-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset attempted.' }
Push-Location $Repo
try {
    & $Python -m app.asset_modules.paper_day_setup --repo $Repo --output (Join-Path $Root 'trial') --profile GROK_WARRIOR_ACCOUNT_V6 --extended-sessions --clock-tolerance
    if ($LASTEXITCODE -ne 0) { throw 'Source installed; fresh manual trial preparation failed. Nothing started.' }
} finally { Pop-Location }
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host 'REPAIR_INSTALLED; ATLAS_LEFT_STOPPED; CRYPTO_NOT_RESTARTED; NO_API_REQUESTS'
Write-Host 'ONLY_PRIVATE_LEDGER_COPY_WAS_PROTECTED_IN_THIS_CHECK; WORKSTATION_POSITION_UNCHANGED'
Write-Host 'EXISTING_TRIALS_BUDGETS_DEADLINES_AND_LEDGERS_RETAINED; EXPIRED_TRIAL_STARTUP_ALLOWS_PROTECTION_ONLY'
Write-Host 'NEW_TRIAL_IS_PREPARED_ONLY; ITS_8_HOUR_CLOCK_STARTS_AT_MANUAL_LAUNCH'
