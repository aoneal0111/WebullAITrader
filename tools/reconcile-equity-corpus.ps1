param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not on the reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\equity-corpus-reconciliation-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Research checkout failed.' }
Push-Location $Source
try {
    & $Python -m unittest tests.strategy_research.test_equity_corpus_reconcile tests.strategy_research.test_equity_corpus_review tests.equity_overhaul.test_metric_semantics_audit -q
    if ($LASTEXITCODE -ne 0) { throw 'Offline checks failed. Installed Atlas unchanged.' }
    Write-Host 'READ_ONLY_SCAN; BOTH_CORPORA; UP_TO_40GB_EACH; THIS_MAY_TAKE_SEVERAL_MINUTES'
    & $Python -m app.strategy_research.equity_corpus_reconcile --repo $Repo --output (Join-Path $Root 'results')
    if ($LASTEXITCODE -ne 0) { throw 'Reconciliation failed. Preserve results/failure.json if present. No source files or manifests were changed.' }
}
finally { Pop-Location }
Write-Host 'SCAN_COMPLETE; NO_STRATEGY_REPLAY_OR_ACTIVATION; NO_GROK_OR_MARKET_DATA_REQUESTS; ATLAS_UNCHANGED'
