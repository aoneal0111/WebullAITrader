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
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\episode-price-study-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Research checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/trade_intelligence/test_chronological_exit_replay.py tests/trade_intelligence/test_exit_replay_entry_sizing.py tests/equity_overhaul/test_whole_share_exit_replay.py tests/strategy_research/test_episode_price_study.py tests/strategy_research/test_episode_miner_prefix_contract.py tests/strategy_research/test_equity_corpus_reconcile.py tests/strategy_research/test_equity_corpus_review.py tests/equity_overhaul/test_metric_semantics_audit.py --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Offline replay checks failed. Installed Atlas unchanged.' }
    Write-Host 'FROZEN_PRICE_STUDY; FIVE_FAMILIES; FOUR_EXIT_METHODS; TWO_ASSUMED_COSTS; NO_BOT_STARTED'
    Write-Host 'INDEXING_VERIFIED_22GB_CORPUS_THEN_REPLAYING_UP_TO_600_SYMBOL_DAYS'
    & $Python -m app.strategy_research.episode_price_study --repo $Repo --output (Join-Path $Root 'results')
    if ($LASTEXITCODE -ne 0) { throw 'Price study failed. Preserve results/failure.json and the error. No source or settings were changed.' }
}
finally { Pop-Location }
Write-Host 'PRICE_STUDY_COMPLETE; PROMOTION=NONE; NO_GROK_OR_MARKET_DATA_REQUESTS; INSTALLED_ATLAS_UNCHANGED'
