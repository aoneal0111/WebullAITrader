param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Review,
    [string]$Fleet = (Join-Path $env:LOCALAPPDATA 'AtlasResearch\crypto-weekend-0db9d940ad9b48d68f0c2f3b5ded0ff5\paper-fleet')
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Fleet = (Resolve-Path -LiteralPath $Fleet).Path
$Review = (Resolve-Path -LiteralPath $Review).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python missing.' }
if (-not (Test-Path -LiteralPath (Join-Path $Fleet 'fleet.json') -PathType Leaf)) { throw 'Existing fleet manifest missing.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\crypto-signal-audit-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
$Zip = Join-Path $Root 'crypto-signal-evidence.zip'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Isolated export checkout failed. No worker changed.' }
Push-Location $Source
try {
    & $Python -m unittest tests.strategy_lab.test_crypto_fleet_evidence -q
    if ($LASTEXITCODE -ne 0) { throw 'Read-only export checks failed. Original recordings unchanged.' }
    Write-Host 'EXPORTING_SIX_VERIFIED_JOURNAL_PREFIXES; MAY_TAKE_A_FEW_MINUTES; WORKERS_LEFT_RUNNING'
    & $Python -m app.strategy_lab.crypto_fleet_evidence --fleet $Fleet --output $Zip --review $Review
    if ($LASTEXITCODE -ne 0) { throw 'Evidence verification/export failed. Preserve the error; no strategy, process or recording was changed.' }
} finally { Pop-Location }
Write-Host "UPLOAD_FILE=$Zip"
Write-Host 'PRIVATE_REPLAY_EVIDENCE; NO_MARKET_DATA_OR_GROK_REQUESTS; NO_ORDER_REQUESTS'
Write-Host 'RUNNING_CONTAINERS_INSTALLED_SOURCE_CONFIGS_LEDGERS_AND_ENTRY_RULES_UNCHANGED'
Invoke-Item $Root
