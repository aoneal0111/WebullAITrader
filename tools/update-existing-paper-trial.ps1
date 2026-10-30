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
        $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|bootstrap-observer\.py|grok_day_proposals'
    }
    if ($Running) { throw 'Close Atlas and stop Grok intentions normally first. Nothing launched.' }
}
function Get-PreservedState {
    $Paths = @('experiment.json','entry-attempts.sqlite3','entry-attempts.sqlite3-wal',
        'entry-attempts.sqlite3-shm','entry-attempts.sqlite3-journal','PAUSE_ENTRIES','STOP_GROK',
        'recording-session.json','Start-Atlas-Paper-Trial.ps1','Start-Grok-Intentions.ps1')
    $State = foreach ($Name in $Paths) {
        $Path = Join-Path $Trial $Name
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [ordered]@{name=$Name;sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash}
        } else { [ordered]@{name=$Name;sha256=$null} }
    }
    $EnvFile = Join-Path $Repo '.env'
    $EnvHash = if (Test-Path -LiteralPath $EnvFile) { (Get-FileHash -LiteralPath $EnvFile -Algorithm SHA256).Hash } else { $null }
    [ordered]@{trial=$State;env_sha256=$EnvHash} | ConvertTo-Json -Depth 5 -Compress
}
Assert-Closed
foreach ($Name in @('experiment.json','entry-attempts.sqlite3','recording-session.json',
    'Start-Atlas-Paper-Trial.ps1','Start-Grok-Intentions.ps1')) {
    if (-not (Test-Path -LiteralPath (Join-Path $Trial $Name) -PathType Leaf)) { throw "Existing trial state missing: $Name. No budget recreated." }
}
$Before = Get-PreservedState
$Dirty = git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Tracked local changes or check failure. Nothing overwritten.' }
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision not on reviewed branch.' }
git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Your branch needs integration. Nothing changed.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\paper-trial-repair-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
$Validate = @'
import sys
from pathlib import Path
from app.equity_overhaul.paper_experiment import load
_, spec = load(Path(sys.argv[1]) / "experiment.json")
if spec.get("profile") != "CENT_EARLY_V2":
    raise ValueError("EXISTING_CENT_EARLY_V2_REQUIRED")
print("EXISTING_TRIAL_VALID; EXPIRY=" + spec["expires_at"])
'@
Push-Location $Source
try {
    $Validate | & $Python - $Trial
    if ($LASTEXITCODE -ne 0) { throw 'Existing trial expired or invalid. No renewal or source update performed.' }
    & $Python -m pytest tests/warrior_momentum/test_cent_scalper_profile.py tests/warrior_momentum/test_quick_scalper.py tests/warrior_momentum/test_quick_scalper_runtime.py tests/asset_modules/test_scalper_evidence_audit.py tests/equity_overhaul/test_cent_early_profile.py tests/equity_overhaul/test_paper_experiment.py tests/asset_modules/test_native_fill_funnel.py tests/warrior_momentum/test_execution_quote_confirmation.py tests/paper_gateway/test_session_aware_entry_validity.py tests/asset_modules/test_daily_research.py tests/composition/test_desktop_composition.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Windows checks failed. Installed source and trial unchanged.' }
    $Validate | & $Python - $Trial
    if ($LASTEXITCODE -ne 0) { throw 'Trial expired during checks. No source update performed.' }
} finally { Pop-Location }
Assert-Closed
if ((Get-PreservedState) -cne $Before) { throw 'Trial or private settings changed during checks. Nothing installed.' }
$Backup = 'backup/before-cent-target-repair-' + [guid]::NewGuid().ToString('N')
git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset performed.' }
if ((Get-PreservedState) -cne $Before) { throw 'State verification failed after source update. Keep Atlas closed; no automatic reset.' }
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host 'EXISTING_TRIAL_ID_EXPIRY_ENTRY_BUDGET_LAUNCHERS_AND_ENV_UNCHANGED; NOTHING_STARTED'
Write-Host ('MANUAL_ATLAS=' + (Join-Path $Trial 'Start-Atlas-Paper-Trial.ps1'))
if (Test-Path -LiteralPath (Join-Path $Trial 'grok-order-intentions')) {
    Write-Host 'PREVIOUS_GROK_RESULTS_RETAINED; EXISTING_GROK_OUTPUT_DIRECTORY_MUST_BE_REVIEWED_BEFORE_RESTART'
} else {
    Write-Host ('MANUAL_GROK=' + (Join-Path $Trial 'Start-Grok-Intentions.ps1'))
}
