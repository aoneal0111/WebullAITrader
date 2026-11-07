param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact research revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\multi-worker-study-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Research checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest -q tests/strategy_research/test_multi_worker_replay.py tests/strategy_research/test_crypto_screening.py
    if ($LASTEXITCODE -ne 0) { throw 'Research tests failed.' }
    # Separate environment inherits installed Atlas libraries; only Arrow is added.
    # Installed Atlas checkout, settings, budgets and launchers are not modified.
    $Environment = Join-Path $Root 'research-python'
    & $Python -m venv --system-site-packages $Environment
    if ($LASTEXITCODE -ne 0) { throw 'Research environment creation failed.' }
    $ResearchPython = Join-Path $Environment 'Scripts\python.exe'
    & $ResearchPython -m pip install --disable-pip-version-check pyarrow==26.0.1
    if ($LASTEXITCODE -ne 0) { throw 'Research Arrow dependency failed.' }
    & $ResearchPython -m app.strategy_research.multi_worker_history --output (Join-Path $Root 'history')
    if ($LASTEXITCODE -ne 0) { throw 'Pinned history capture incomplete; preserve research directory.' }
    $Report = Join-Path $Root 'multi-worker-summary.json'
    & $ResearchPython -m app.strategy_research.multi_worker_replay --history (Join-Path $Root 'history') --output $Report
    if ($LASTEXITCODE -ne 0) { throw 'Offline replay failed; Atlas unchanged.' }
    Write-Host "UPLOAD_SUMMARY=$Report"
    Write-Host 'OFFLINE_RESEARCH_ONLY; NO_BOT_OR_GROK_STARTED; NO_ORDERS; NO_STRATEGY_PROMOTED'
} finally { Pop-Location }
