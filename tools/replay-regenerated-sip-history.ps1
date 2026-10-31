param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$History
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$History = (Resolve-Path -LiteralPath $History).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision not on reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\sip-regenerated-replay-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
git -C $Repo worktree add --detach (Join-Path $Root 'review-source') $Revision
if ($LASTEXITCODE -ne 0) { throw 'Research checkout failed.' }
Push-Location (Join-Path $Root 'review-source')
try {
    & $Python -m pytest tests/strategy_research/test_sip_regenerated_replay.py tests/strategy_research/test_episode_price_study.py tests/strategy_research/test_episode_miner_prefix_contract.py tests/trade_intelligence/test_chronological_exit_replay.py tests/trade_intelligence/test_exit_replay_entry_sizing.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Offline replay checks failed.' }
    & $Python -m app.strategy_research.sip_regenerated_replay --history $History --output (Join-Path $Root 'results')
    if ($LASTEXITCODE -ne 0) { throw 'Replay failed; preserve results/failure.json. Atlas unchanged.' }
} finally { Pop-Location }
Write-Host 'SIP_REPLAY_COMPLETE; OFFLINE_ONLY; PROMOTION=NONE; ATLAS_UNCHANGED'
