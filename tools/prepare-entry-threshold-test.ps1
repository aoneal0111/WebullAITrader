param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [switch]$PrepareAtlas
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Atlas virtual environment missing.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\entry-threshold-test-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Isolated source checkout failed.' }
$Image = 'freqtradeorg/freqtrade:2026.9@sha256:989aff5636d8f0213ed44fe334c3314a194a51c0c755d16866960247aafd2eae'
& docker image inspect $Image > $null
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop; pinned image required.' }
& docker run --rm --network none --read-only --tmpfs /tmp --workdir /check --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" $Image -m unittest tests.strategy_lab.test_paper_candidate_addon -q
if ($LASTEXITCODE -ne 0) { throw 'Disconnected paper-flow checks failed. Nothing started.' }
Push-Location $Source
try {
    & $Python -m app.strategy_lab.equity_entry_pilot --root (Join-Path $Root 'entry-test') --source $Source
    if ($LASTEXITCODE -ne 0) { throw 'Entry comparison preparation failed.' }
} finally { Pop-Location }
& docker compose -f (Join-Path $Root 'entry-test\compose.json') config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose validation failed.' }
if ($PrepareAtlas) {
    & git -C $Repo merge-base --is-ancestor '11abe7be440ebe1d5f8f200c9e81bb2072e6ae47' HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Install the existing equity timestamp repair before preparing Atlas.' }
    Push-Location $Repo
    try {
        & $Python -m app.asset_modules.paper_day_setup --repo $Repo --output (Join-Path $Root 'atlas-trial') --profile GROK_WARRIOR_ACCOUNT_V6 --extended-sessions
        if ($LASTEXITCODE -ne 0) { throw 'Fresh Atlas trial preparation failed; see diagnostic above.' }
    } finally { Pop-Location }
}
Write-Host "PREPARATION_DIRECTORY=$Root"
Write-Host 'PREPARED_ONLY; NOTHING_STARTED; EXISTING_CRYPTO_WALLETS_AND_WORKERS_UNCHANGED'
Write-Host 'Crypto entry relaxation failed profit checks; no losing threshold installed.'
