param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$History
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'An exact reviewed revision is required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$History = (Resolve-Path -LiteralPath $History).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python is missing.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not on the reviewed research branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\crypto-candidate-screen-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Research checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/strategy_lab --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Research checks failed. Nothing was started.' }
    $Lock = Get-Content -LiteralPath 'app\strategy_lab\candidate-source-lock.json' -Raw | ConvertFrom-Json
    $Upstream = Join-Path $Root 'pinned-candidates'
    & git init $Upstream
    if ($LASTEXITCODE -ne 0) { throw 'Candidate checkout initialization failed.' }
    & git -C $Upstream config core.autocrlf false
    if ($LASTEXITCODE -ne 0) { throw 'Pinned source byte-preservation setup failed.' }
    & git -C $Upstream fetch --depth 1 $Lock.repository $Lock.commit
    if ($LASTEXITCODE -ne 0) { throw 'Pinned candidate download failed.' }
    & git -C $Upstream checkout --detach FETCH_HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Pinned candidate checkout failed.' }
    $Actual = & git -C $Upstream rev-parse HEAD
    if ($Actual -ne $Lock.commit) { throw 'Candidate commit mismatch.' }
    $Results = Join-Path $Root 'results'
    $null = New-Item -ItemType Directory -Path $Results
    & $Python -m app.strategy_lab.candidate_screen --source $Upstream --history $History --output $Results --verify-only
    if ($LASTEXITCODE -ne 0) { throw 'Source/history verification failed. No replay was started.' }
}
finally { Pop-Location }
$RuntimeLock = Get-Content -LiteralPath (Join-Path $Source 'app\strategy_lab\source-lock.json') -Raw | ConvertFrom-Json
$StudySource = Join-Path $Source 'app\strategy_lab'
Write-Host "RESEARCH_DIRECTORY=$Root"
Write-Host 'REUSING_FROZEN_HISTORY; NO_MARKET_DATA_DOWNLOAD; NO_GROK_REQUESTS'
& docker run --rm --network none --entrypoint python `
    --mount "type=bind,source=$Upstream,target=/candidates,readonly" `
    --mount "type=bind,source=$StudySource,target=/study-source,readonly" `
    --mount "type=bind,source=$History,target=/history,readonly" `
    --mount "type=bind,source=$Results,target=/results" `
    $RuntimeLock.image /study-source/candidate_screen.py --source /candidates --history /history --output /results/screen
if ($LASTEXITCODE -ne 0) { throw 'Offline candidate comparison failed. Preserve its summary and final error. No trading settings changed.' }
Write-Host "UPLOAD_SUMMARY=$(Join-Path $Results 'screen\candidate-screen-summary.json')"
Write-Host 'CANDIDATE_COMPARISON_FINISHED; NO_ACTIVATION; ATLAS_AND_NFI_BASELINES_UNCHANGED'
