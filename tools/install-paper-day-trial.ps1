param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [ValidateSet('LEGACY_V1','CENT_EARLY_V2','CENT_EXECUTABLE_V3')][string]$ExperimentProfile = 'LEGACY_V1',
    [string]$RecordedSession = ''
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
if ($ExperimentProfile -eq 'CENT_EXECUTABLE_V3') {
    if (-not $RecordedSession) { throw 'V3 requires a closed RecordedSession for the offline blocker comparison.' }
    $RecordedSession = (Resolve-Path -LiteralPath $RecordedSession).Path
    if (-not (Test-Path -LiteralPath (Join-Path $RecordedSession 'research-summary.json'))) {
        throw 'Final recording summary missing. Close Atlas normally first.'
    }
}
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|bootstrap-observer\.py|grok_day_proposals'
}
if ($Running) { throw 'Close Atlas and stop Grok intentions normally before installing. Nothing changed.' }
$Dirty = git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Tracked repository changes or check failure. Nothing overwritten.' }
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision not on reviewed branch.' }
git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Your branch needs integration. Nothing changed.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\paper-day-install-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/warrior_momentum/test_cent_stream_support.py tests/warrior_momentum/test_cent_executable_v3.py tests/asset_modules/test_scalper_repair_replay.py tests/equity_overhaul/test_paper_experiment.py tests/equity_overhaul/test_cent_early_profile.py tests/strategy_research/test_exit_trace.py tests/warrior_momentum/test_strategy.py tests/warrior_momentum/test_point_setup_continuity.py tests/warrior_momentum/test_configuration_provenance.py tests/warrior_momentum/test_setup_diagnostics.py tests/asset_modules/test_native_fill_funnel.py tests/warrior_momentum/test_cent_scalper_profile.py tests/warrior_momentum/test_quick_scalper_runtime.py tests/warrior_momentum/test_profit_retention_shadow.py tests/equity_overhaul/test_admission.py tests/asset_modules/test_equity_policy_attribution.py tests/asset_modules/test_daily_research.py tests/asset_modules/test_daily_observer_protocol.py tests/asset_modules/test_grok_day_proposals.py tests/asset_modules/test_paper_day_setup.py tests/gui/test_workstation_composition.py tests/composition/test_desktop_composition.py tests/asset_modules/test_grok_observer_capture.py tests/asset_modules/test_grok_observer_session.py tests/warrior_momentum/test_execution_quote_provenance.py tests/asset_modules/test_scalper_evidence_audit.py tests/asset_modules/test_grok_execution_trial.py tests/asset_modules/test_grok_execution_crypto.py tests/asset_modules/test_grok_next_window.py tests/warrior_momentum/test_execution_quote_confirmation.py tests/paper_gateway/test_session_aware_entry_validity.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Windows checks failed. Installed source and settings unchanged.' }
    if ($ExperimentProfile -eq 'CENT_EXECUTABLE_V3') {
        & $Python -m app.asset_modules.scalper_repair_replay --session $RecordedSession --output (Join-Path $Root 'scalper-repair-replay.json')
        if ($LASTEXITCODE -ne 0) { throw 'Frozen blocker comparison failed. Installed source and settings unchanged.' }
    }
} finally { Pop-Location }
$Backup = 'backup/before-paper-day-trial-' + [guid]::NewGuid().ToString('N')
git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset performed.' }
Push-Location $Repo
try {
    & $Python -m app.asset_modules.paper_day_setup --repo $Repo --output (Join-Path $Root 'trial') --profile $ExperimentProfile
    if ($LASTEXITCODE -ne 0) { throw 'Source installed; trial preparation failed. Nothing launched.' }
} finally { Pop-Location }
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host 'PREPARED_ONLY; ATLAS_NOT_STARTED; NO_API_REQUESTS; NO_ENV_FILE_CHANGED'
Write-Host 'Run MANUAL_ATLAS yourself, click START normally, then MANUAL_GROK in a second shell.'
