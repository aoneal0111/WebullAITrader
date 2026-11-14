param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Review
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Review = (Resolve-Path -LiteralPath $Review).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\equity-entry-evidence-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Read-only review checkout failed.' }
Push-Location $Source
try {
    & $Python -m unittest tests.strategy_research.test_equity_entry_evidence -q
    if ($LASTEXITCODE -ne 0) { throw 'Evidence extraction tests failed.' }
    & $Python -m app.strategy_research.equity_entry_evidence --review $Review --output (Join-Path $Root 'equity-entry-evidence.zip')
    if ($LASTEXITCODE -ne 0) { throw 'Evidence extraction failed; original files unchanged.' }
} finally { Pop-Location }
Invoke-Item $Root
