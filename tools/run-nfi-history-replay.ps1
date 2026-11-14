param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Lab,
    [string]$ResumeHistory
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'An exact reviewed revision is required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Lab = (Resolve-Path -LiteralPath $Lab).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python is missing.' }
if (-not (Test-Path -LiteralPath (Join-Path $Lab 'lab-manifest.json') -PathType Leaf)) { throw 'Prepared lab manifest is missing.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not in the reviewed research branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\nfi-history-replay-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Study checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/strategy_lab --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Study checks failed. Nothing was changed.' }
    & $Python -m app.strategy_lab.supervisor --repo $Repo --lab $Lab --check
    if ($LASTEXITCODE -ne 0) { throw 'Original lab verification failed. Nothing was changed.' }
    Write-Host "RESEARCH_DIRECTORY=$Root"
    $Study = if ($ResumeHistory) { (Resolve-Path -LiteralPath $ResumeHistory).Path } else { Join-Path $Root 'frozen-history' }
    Write-Host "HISTORY_DIRECTORY=$Study"
    Write-Host 'ARCHIVE_CAPTURE_STARTED; SELECTED_MEMBERS_ONLY; DOWNLOAD_BOUND=512_MiB'
    if ($ResumeHistory) {
        $Prior = Get-Content -LiteralPath (Join-Path $Study 'history-manifest.json') -Raw | ConvertFrom-Json
        if ($Prior.status -eq 'COMPLETE') {
            Write-Host 'REUSING_COMPLETE_HISTORY; OFFLINE_REPLAY_WILL_VERIFY_ALL_HASHES'
        } else {
            & $Python -m app.strategy_lab.archive_history --output $Study --resume
        }
    } else {
        & $Python -m app.strategy_lab.archive_history --output $Study
    }
    if ($LASTEXITCODE -ne 0) { throw 'Archive import failed; history-manifest.json preserves diagnostics. No entries were enabled.' }
}
finally { Pop-Location }
$Lock = Get-Content -LiteralPath (Join-Path $Source 'app\strategy_lab\source-lock.json') -Raw | ConvertFrom-Json
$StudySource = Join-Path $Source 'app\strategy_lab'
$Results = Join-Path $Root 'results'
$null = New-Item -ItemType Directory -Path $Results
& docker run --rm --network none --entrypoint python `
    --mount "type=bind,source=$Lab,target=/lab,readonly" `
    --mount "type=bind,source=$StudySource,target=/study-source,readonly" `
    --mount "type=bind,source=$Study,target=/history,readonly" `
    --mount "type=bind,source=$Results,target=/results" `
    $Lock.image /study-source/history_replay.py --lab /lab --history /history --output /results/replay
if ($LASTEXITCODE -ne 0) { throw 'Offline historical replay failed; send its final error and any replay summary. No trading process or Grok manager was started.' }
Write-Host "UPLOAD_SUMMARY=$(Join-Path $Results 'replay\historical-replay-summary.json')"
Write-Host 'STUDY_FINISHED; NO_TRADING_LOOP; NO_GROK_REQUESTS; EXISTING_LAB_AND_ATLAS_UNCHANGED'
