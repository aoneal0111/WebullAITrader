param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Session
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Session = (Resolve-Path -LiteralPath $Session).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not on the reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\cent-portfolio-study-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Research checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/equity_overhaul/test_cent_scalper.py tests/equity_overhaul/test_cent_portfolio.py tests/asset_modules/test_grok_observer_runtime_tap.py tests/asset_modules/test_daily_observer_protocol.py tests/warrior_momentum/test_desktop_sidecar.py tests/asset_modules/test_daily_research.py tests/warrior_momentum/test_execution_quote_confirmation.py tests/warrior_momentum/test_quick_scalper_runtime.py --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Replay verification failed. Installed Atlas unchanged.' }
    & $Python -m app.equity_overhaul.cent_journal_replay --session $Session --output (Join-Path $Root 'results')
    if ($LASTEXITCODE -ne 0) { throw 'Replay failed; preserve the error. No settings or recordings changed.' }
}
finally { Pop-Location }
Write-Host 'OFFLINE_RESEARCH_ONLY; NO_BOT_STARTED; NO_API_REQUESTS; NO_STRATEGY_PROMOTED'
