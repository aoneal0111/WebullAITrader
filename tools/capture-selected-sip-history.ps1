param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Study
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Study = (Resolve-Path -LiteralPath $Study).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision not on reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\selected-sip-history-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
git -C $Repo worktree add --detach (Join-Path $Root 'review-source') $Revision
if ($LASTEXITCODE -ne 0) { throw 'Research checkout failed.' }
Push-Location (Join-Path $Root 'review-source')
try {
    & $Python -m unittest tests.strategy_research.test_selected_sip_capture tests.strategy_research.test_episode_gap_audit -q
    if ($LASTEXITCODE -ne 0) { throw 'Capture checks failed.' }
    & $Python -m app.strategy_research.selected_sip_capture --repo $Repo --study $Study --output (Join-Path $Root 'history')
    if ($LASTEXITCODE -ne 0) { throw 'Capture incomplete. Preserve the printed summary or error; no subscription or fallback was attempted.' }
} finally { Pop-Location }
Write-Host 'SIP_HISTORY_SAVED_SEPARATELY; ORIGINAL_DATA_AND_ATLAS_UNCHANGED; NO_TRADING_OR_GROK_REQUESTS'
