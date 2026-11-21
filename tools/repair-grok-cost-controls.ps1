param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$PaperDirectory,
    [string]$DailyBudgetUsd = "1.00"
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
# Nested recording fixtures include their own session and private-capture paths.
# Keep this root short for Windows hosts without long-path support.
$Root = Join-Path $env:LOCALAPPDATA ('AtlasChecks\gc-' + [guid]::NewGuid().ToString('N').Substring(0,12))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
$PreviousQt = [Environment]::GetEnvironmentVariable('QT_QPA_PLATFORM','Process')
Push-Location $Source
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    & $Python -c "import sys; from pathlib import Path; from app.asset_modules.grok_cost_policy import policy; policy(sys.argv[2]); from app.equity_overhaul.paper_experiment import load, CONTINUOUS_SCOPE; assert load(Path(sys.argv[1]) / 'experiment.json', allow_expired=True)[1]['scope'] == CONTINUOUS_SCOPE" $PaperDirectory $DailyBudgetUsd
    if ($LASTEXITCODE -ne 0) { throw 'Existing continuous PAPER spec and valid daily budget required. Nothing changed.' }
    & $Python -m pytest tests/asset_modules/test_grok_cost_policy.py tests/asset_modules/test_grok_engine_teams.py tests/asset_modules/test_grok_payment_required.py tests/asset_modules/test_grok_rate_limit.py tests/asset_modules/test_grok_failure.py tests/asset_modules/test_grok.py tests/warrior_momentum tests/opportunity_discovery tests/strategies/test_autonomous_paper_bridge.py tests/composition/test_warrior_quote_routing.py tests/composition/test_desktop_composition.py tests/asset_modules/test_grok_warrior_control.py tests/asset_modules/test_continuous_paper.py tests/asset_modules/test_controlled_paper_service.py tests/asset_modules/test_daily_research.py tests/asset_modules/test_grok_observer_session.py tests/asset_modules/test_grok_observer_runtime_tap.py tests/asset_modules/test_warrior_taxonomy_entry_path.py tests/asset_modules/test_paper_morning_status.py tests/asset_modules/test_grok_native_review.py tests/asset_modules/test_native_review_branches.py tests/asset_modules/test_grok_desktop_producer.py tests/asset_modules/test_grok_runtime_handoff.py tests/asset_modules/test_grok_paper_entries.py tests/equity_overhaul/test_admission.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Grok cost, review, Warrior entry and protection checks failed. Installed source unchanged.' }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM',$PreviousQt,'Process')
    Pop-Location
}
$Backup = 'backup/before-grok-cost-repair-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset attempted.' }
Push-Location $Repo
try {
    & $Python -m app.asset_modules.grok_cost_policy --paper $PaperDirectory --daily-usd $DailyBudgetUsd
    if ($LASTEXITCODE -ne 0) { throw 'Source installed but cost policy setup failed. Keep Atlas closed and preserve this output.' }
} finally { Pop-Location }
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host "MANUAL_ATLAS=$Launcher"
Write-Host 'INSTALLATION_CHECKS_OFFLINE; TRADING_LEFT_STOPPED; NO_ENV_FILE_CHANGED'
Write-Host 'EXISTING_PERSISTENT_OPERATION_ID_LAUNCHERS_AND_TRADING_LEDGERS_UNCHANGED; COST_POLICY_FILE_WRITTEN; CRYPTO_NOT_RESTARTED'
Write-Host 'GROK_ESTIMATED_DAILY_BUDGET_ENABLED; UTC_RESET; DIRECTOR_SHARE_25_PERCENT; NATIVE_REVIEWS_PRIORITIZED'
Write-Host ('READ_ONLY_STATUS=' + (Join-Path $Repo 'tools\Show-Warrior-Entry-Status.ps1'))
Write-Host 'GROK_CALL_SCHEDULING_AND_ESTIMATED_COST_POLICY_UPDATED; STRATEGY_PARAMETERS_AND_OPERATING_SPEC_UNCHANGED; LIVE_OFF'
Write-Host 'SCALPER_CRYPTO_STOPS_TARGETS_AND_STRATEGY_PARAMETERS_UNCHANGED'
Write-Host 'NO_PROVIDER_REQUESTS; EXTERNAL_ENTRY_FLOW_AND_PROFITABILITY_REQUIRE_WORKSTATION_EVIDENCE'
Write-Host ('DAILY_ESTIMATED_BUDGET_USD=' + $DailyBudgetUsd)
Write-Host 'ESTIMATED_LOCAL_RESERVATIONS_ARE_NOT_AN_XAI_INVOICE_CAP; SET_PROVIDER_ACCOUNT_SPENDING_LIMIT_SEPARATELY'
