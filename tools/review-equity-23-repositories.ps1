param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Report = Join-Path $Repo 'data\research\historical_tranches\2026_06_01__2026_08_31\reports\full_research_final.json'
if (-not (Test-Path -LiteralPath $Report -PathType Leaf)) {
    throw 'The historical 23-strategy report is missing at the known path. No acquisition or settings changes were made.'
}
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not on the reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\equity-23-repository-review-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Research checkout failed.' }
Push-Location $Source
try {
    & $Python -m unittest tests.strategy_research.test_equity_corpus_review tests.equity_overhaul.test_metric_semantics_audit -q
    if ($LASTEXITCODE -ne 0) { throw 'Offline checks failed. Installed Atlas unchanged.' }
    & $Python -m app.strategy_research.equity_corpus_review --repo $Repo --output (Join-Path $Root 'results')
    if ($LASTEXITCODE -ne 0) { throw 'Report review failed. Preserve the error; no strategy was enabled.' }
}
finally { Pop-Location }
Write-Host 'REVIEW_COMPLETE; NO_BOT_STARTED; NO_GROK_OR_MARKET_DATA_REQUESTS; INSTALLED_ATLAS_UNCHANGED'
