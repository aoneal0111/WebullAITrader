param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Trial
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Trial = (Resolve-Path -LiteralPath $Trial).Path
$SummaryPath = Join-Path $Trial 'preparation-summary.json'
$Summary = Get-Content -LiteralPath $SummaryPath -Raw | ConvertFrom-Json
if ($Summary.scope -ne 'MANUAL_PAPER_DAY_TRIAL_PREPARATION_V1' -or
    $Summary.shared_paid_attempt_cap -notin @(20,60) -or
    -not (Test-Path -LiteralPath (Join-Path $Trial 'Start-Grok-Teams.ps1'))) {
    throw 'Existing explicit morning team trial required.'
}
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|bootstrap-observer\.py|grok_day_proposals'
}
if ($Running) { throw 'Close Atlas and Grok normally before updating. No process was stopped.' }
$Dirty = git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Tracked changes or repository check failure. Nothing overwritten.' }
$Preserved = @{}
foreach ($Path in @((Join-Path $Repo '.env'),(Join-Path $Trial 'experiment.json'),
    (Join-Path $Trial 'entry-attempts.sqlite3'),(Join-Path $Trial 'grok-team-budget.sqlite3'))) {
    if (Test-Path -LiteralPath $Path) { $Preserved[$Path] = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }
}
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
& git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Branch integration required; no reset performed.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\morning-budget-update-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
Push-Location $Source
try {
    & $Python -m pytest tests/asset_modules/test_grok_observer_capture.py tests/asset_modules/test_daily_research.py tests/asset_modules/test_grok_engine_teams.py tests/asset_modules/test_grok_day_proposals.py tests/asset_modules/test_paper_day_setup.py tests/asset_modules/test_paper_morning_status.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Checks failed. Installed source and trial unchanged.' }
} finally { Pop-Location }
$Backup = 'backup/before-morning-budget-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset performed.' }
foreach ($Path in $Preserved.Keys) {
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Preserved[$Path]) {
        throw 'An existing trial, budget or environment file changed during update. Nothing reset.'
    }
}
Copy-Item -LiteralPath $SummaryPath -Destination (Join-Path $Root 'preparation-summary-before.json')
$Summary | Add-Member -NotePropertyName shared_paid_attempt_cap -NotePropertyValue 60 -Force
$Summary | Add-Member -NotePropertyName engine_attempt_cap -NotePropertyValue 40 -Force
$Summary | Add-Member -NotePropertyName per_engine_attempt_cap -NotePropertyValue 20 -Force
$Summary | Add-Member -NotePropertyName news_scout_attempt_cap -NotePropertyValue 20 -Force
$Summary | Add-Member -NotePropertyName observations -NotePropertyValue 'Durable hourly engine slots over the original eight-hour trial; no all-signal review' -Force
$Summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $SummaryPath -Encoding UTF8
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host 'GROK_BUDGET=20_MOMENTUM_20_SCALPER_20_NEWS_SCOUT; TOTAL=60'
Write-Host 'EXISTING_TRIAL_EXPIRY_ENTRY_BUDGET_MODEL_CALL_ROWS_AND_ENV_PRESERVED; NOTHING_STARTED'
Write-Host ('MANUAL_ATLAS=' + (Join-Path $Trial 'Start-Atlas-Paper-Trial.ps1'))
Write-Host ('MANUAL_GROK=' + (Join-Path $Trial 'Start-Grok-Teams.ps1'))
Write-Host ('READ_ONLY_STATUS=' + (Join-Path $Trial 'Show-Morning-Status.ps1'))
