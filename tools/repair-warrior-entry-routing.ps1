param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$PaperDirectory,
    [switch]$CaptureStoppedRun
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
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\warrior-entry-routing-repair-' + [guid]::NewGuid().ToString('N'))
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
    if ($CaptureStoppedRun) {
        try { & (Join-Path $Source 'tools\Capture-Warrior-Entry-Gates.ps1') -PaperDirectory $PaperDirectory }
        catch { Write-Warning ('Stopped-run capture failed; repair checks continue: ' + $_.Exception.Message) }
    }
    & $Python -m pytest tests/warrior_momentum tests/strategies/test_autonomous_paper_bridge.py tests/composition/test_warrior_quote_routing.py tests/composition/test_desktop_composition.py tests/asset_modules/test_grok_warrior_control.py tests/asset_modules/test_continuous_paper.py tests/asset_modules/test_controlled_paper_service.py tests/asset_modules/test_daily_research.py tests/asset_modules/test_grok_observer_session.py tests/asset_modules/test_grok_observer_runtime_tap.py tests/equity_overhaul/test_admission.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Warrior entry, quote and protection checks failed. Installed source unchanged.' }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM',$PreviousQt,'Process')
    Pop-Location
}
$Backup = 'backup/before-warrior-entry-routing-repair-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset attempted.' }
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host "MANUAL_ATLAS=$Launcher"
Write-Host 'INSTALLATION_CHECKS_OFFLINE; TRADING_LEFT_STOPPED; NO_ENV_FILE_CHANGED'
Write-Host 'EXISTING_PERSISTENT_OPERATION_ID_LAUNCHERS_SETTINGS_AND_LEDGER_UNCHANGED; CRYPTO_NOT_RESTARTED'
Write-Host 'WARRIOR_QUOTE_SELECTION_MATCHES_EXISTING_FINAL_GATE; TAXONOMY_REASSESSED_AFTER_REFRESH'
Write-Host 'STRATEGY_PARAMETERS_GROK_REVIEW_POLICY_AND_OPERATING_SPEC_UNCHANGED; LIVE_OFF'
Write-Host 'SCALPER_CRYPTO_GROK_POLICY_STOPS_TARGETS_AND_STRATEGY_PARAMETERS_UNCHANGED'
Write-Host 'NO_PROVIDER_REQUESTS; EXTERNAL_ENTRY_FLOW_AND_PROFITABILITY_REQUIRE_WORKSTATION_EVIDENCE'
