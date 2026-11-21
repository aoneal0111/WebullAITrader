param(
    [string]$Repo=(Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [string]$Fleet=(Join-Path $env:LOCALAPPDATA 'AtlasResearch\crypto-weekend-0db9d940ad9b48d68f0c2f3b5ded0ff5\paper-fleet')
)
$ErrorActionPreference='Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact revision required.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
$Root=Join-Path $env:LOCALAPPDATA ('AtlasResearch\bollinger-paper-' + [guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $Root
$Source=Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Isolated checkout failed.' }
$Image='freqtradeorg/freqtrade:2026.9@sha256:989aff5636d8f0213ed44fe334c3314a194a51c0c755d16866960247aafd2eae'
& docker run --rm --network none --read-only --tmpfs /tmp --workdir /check --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" $Image -m unittest tests.strategy_lab.test_bollinger_paper_pilot -q
if ($LASTEXITCODE -ne 0) { throw 'Disconnected native checks failed. Nothing started.' }
$Pilot=Join-Path $Root 'pilot'
Push-Location $Source
try {
    & "$Repo\.venv\Scripts\python.exe" -m app.strategy_lab.bollinger_paper_pilot --root $Pilot --fleet $Fleet --source $Source
    if ($LASTEXITCODE -ne 0) { throw 'Preparation failed.' }
} finally { Pop-Location }
$Work=Join-Path $Pilot 'paper'
& docker run --rm --network none --read-only --tmpfs /tmp --workdir /check --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" --mount "type=bind,source=$Work,target=/work" $Image -m app.strategy_lab.crypto_fleet_runtime --work /work --check
if ($LASTEXITCODE -ne 0) { throw 'Native profile check failed. Nothing started.' }
& docker compose -f (Join-Path $Pilot 'compose.json') config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose validation failed.' }
Write-Host 'PREPARED_ONLY; EXISTING_BOTS_AND_WALLETS_UNCHANGED; NO_API_REQUESTS_OR_ORDERS'
Write-Host "MANUAL_START=$(Join-Path $Pilot 'Start-Bollinger-Paper.ps1')"
Write-Host "READ_ONLY_STATUS=$(Join-Path $Pilot 'Show-Bollinger-Paper.ps1')"
