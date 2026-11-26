param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Lab
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
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\nfi-signal-study-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Study checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/strategy_lab --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Study checks failed. Nothing was changed.' }
    $Study = Join-Path $Root 'frozen-candles'
    & $Python -m app.strategy_lab.signal_study --capture --lab $Lab --output $Study
    if ($LASTEXITCODE -ne 0) { throw 'Public capture failed; capture-manifest.json preserves diagnostics. No entries were enabled.' }
}
finally { Pop-Location }
$Lock = Get-Content -LiteralPath (Join-Path $Source 'app\strategy_lab\source-lock.json') -Raw | ConvertFrom-Json
$StudySource = Join-Path $Source 'app\strategy_lab'
& docker run --rm --network none --entrypoint python `
    --mount "type=bind,source=$Lab,target=/lab,readonly" `
    --mount "type=bind,source=$StudySource,target=/study-source,readonly" `
    --mount "type=bind,source=$Study,target=/study" `
    $Lock.image /study-source/signal_study.py --analyze --lab /lab --output /study
if ($LASTEXITCODE -ne 0) { throw 'Offline signal analysis failed. No trading process or Grok manager was started.' }
Write-Host "UPLOAD_SUMMARY=$(Join-Path $Study 'signal-comparison.json')"
Write-Host 'STUDY_FINISHED; NO_TRADING_LOOP; NO_GROK_REQUESTS; EXISTING_LAB_AND_ATLAS_UNCHANGED'
