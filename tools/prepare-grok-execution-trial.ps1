param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Trial
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Trial = (Resolve-Path -LiteralPath $Trial).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
function Assert-Closed {
    $Running = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|bootstrap-observer\.py|grok_day_proposals|grok_execution_trial|grok_online_observer'
    }
    if ($Running) { throw 'Close Atlas and stop existing Grok workers normally first. Nothing launched.' }
}
function Preserved-State {
    $Files = @('experiment.json','entry-attempts.sqlite3','entry-attempts.sqlite3-wal','entry-attempts.sqlite3-shm',
        'entry-attempts.sqlite3-journal','PAUSE_ENTRIES','STOP_GROK','recording-session.json',
        'Start-Atlas-Paper-Trial.ps1','Start-Grok-Intentions.ps1')
    $Rows = foreach ($Name in $Files) {
        $Path = Join-Path $Trial $Name
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [ordered]@{name=$Name;sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash}
        } else { [ordered]@{name=$Name;sha256=$null} }
    }
    $EnvFile = Join-Path $Repo '.env'
    $EnvHash = if (Test-Path -LiteralPath $EnvFile) { (Get-FileHash -LiteralPath $EnvFile -Algorithm SHA256).Hash } else { $null }
    [ordered]@{trial=$Rows;env=$EnvHash} | ConvertTo-Json -Depth 5 -Compress
}
Assert-Closed
$Before = Preserved-State
$Dirty = git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Tracked local changes or check failure. Nothing overwritten.' }
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision not on reviewed branch.' }
git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Branch needs integration. Nothing changed.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\grok-execution-install-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Pinned review checkout failed.' }
Push-Location $Source
try {
    & $Python -m app.asset_modules.grok_execution_trial --repo $Repo --trial $Trial --preflight
    if ($LASTEXITCODE -ne 0) { throw 'Execution preflight failed. Source unchanged; no settings changed or paid requests.' }
    & $Python -m pytest tests/asset_modules/test_grok_compact_review.py tests/asset_modules/test_grok_execution_trial.py tests/asset_modules/test_grok_execution_crypto.py tests/asset_modules/test_grok_paper_entries.py tests/asset_modules/test_grok_native_review.py tests/asset_modules/test_grok_entry_ownership.py tests/asset_modules/test_grok_desktop_producer.py tests/asset_modules/test_grok_desktop_completions.py tests/asset_modules/test_grok_desktop_configuration.py tests/asset_modules/test_grok_decision_council.py tests/asset_modules/test_grok_learning.py tests/asset_modules/test_crypto_allocation.py tests/asset_modules/test_crypto_durable_intents.py tests/asset_modules/test_crypto_supervisor_allocation.py tests/asset_modules/test_daily_research.py tests/asset_modules/test_daily_observer_protocol.py tests/equity_overhaul/test_paper_experiment.py tests/equity_overhaul/test_cent_early_profile.py tests/composition/test_desktop_composition.py tests/asset_modules/test_native_crypto_desktop_composition.py tests/warrior_momentum/test_cent_stream_support.py tests/asset_modules/test_supervisor.py tests/crypto_research/test_runtime.py tests/crypto_research/test_provider.py tests/asset_modules/test_grok.py tests/gui/test_workstation_composition.py tests/asset_modules/test_crypto_quote_ownership.py tests/asset_modules/test_crypto_native_observer.py tests/asset_modules/test_crypto_worker.py tests/asset_modules/test_crypto_learning_outcomes.py tests/asset_modules/test_desktop_crypto_allocation.py tests/asset_modules/test_desktop_markets.py tests/asset_modules/test_market_analysis.py tests/asset_modules/test_markets.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Windows checks failed. Installed source unchanged; keep Atlas closed.' }
    & $Python -m app.asset_modules.grok_execution_trial --repo $Repo --trial $Trial --preflight
    if ($LASTEXITCODE -ne 0) { throw 'Preflight no longer valid. Nothing installed.' }
} finally { Pop-Location }
Assert-Closed
if ((Preserved-State) -cne $Before) { throw 'Trial or private settings changed during checks. Nothing installed.' }
$Backup = 'backup/before-grok-execution-' + [guid]::NewGuid().ToString('N')
git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset performed.' }
Push-Location $Repo
try {
    & $Python -m app.asset_modules.grok_execution_trial --repo $Repo --trial $Trial --write-launchers
    if ($LASTEXITCODE -ne 0) { throw 'Source installed but pilot preparation failed. Nothing launched; original launchers retained.' }
} finally { Pop-Location }
if ((Preserved-State) -cne $Before) { throw 'Preserved state verification failed. Keep Atlas closed; no automatic reset.' }
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host 'PREPARED_ONLY; NO_ATLAS_OR_GROK_STARTED; NO_API_REQUESTS; ORIGINAL_TRIAL_AND_ENTRY_BUDGET_UNCHANGED'
Write-Host 'Use the NEW execution launcher manually, then click START. Crypto needs its visible module and AI-entry switches.'
Write-Host 'Do not run the old Grok intentions process concurrently. A paused/expired pilot never disables protection.'
