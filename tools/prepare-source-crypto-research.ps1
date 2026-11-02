param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python missing.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is outside the reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\source-crypto-research-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
$Nfi = Join-Path $Root 'nfi-source'
& git init $Nfi
if ($LASTEXITCODE -ne 0) { throw 'NFI checkout initialization failed.' }
& git -C $Nfi config core.autocrlf false
if ($LASTEXITCODE -ne 0) { throw 'Source byte-preservation setup failed.' }
& git -C $Nfi fetch --depth 1 https://github.com/iterativv/NostalgiaForInfinity.git bbddbd94f97f3c14336387d2958183870891b951
if ($LASTEXITCODE -ne 0) { throw 'Pinned NFI fetch failed.' }
& git -C $Nfi checkout --detach FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Pinned NFI checkout failed.' }
$Work = Join-Path $Root 'backtest'
$History = Join-Path $Root 'author-history'
$News = Join-Path $Root 'news-scout'
Push-Location $Source
try {
    & $Python -m pytest tests/asset_modules/test_grok_news_scout.py tests/strategy_lab/test_nfi_source_reproduction.py tests/strategy_lab/test_nfi_history_import.py tests/asset_modules/test_grok.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Research checks failed. Nothing started.' }
    & $Python -m app.strategy_lab.nfi_source_reproduction --source $Nfi --work $Work
    if ($LASTEXITCODE -ne 0) { throw 'Source/configuration verification failed.' }
}
finally { Pop-Location }
$Quote = { param($Value) "'" + $Value.Replace("'", "''") + "'" }
$Backtest = @'
$ErrorActionPreference = 'Stop'
Push-Location __SOURCE__
try {
    $Image = 'freqtradeorg/freqtrade:2026.9@sha256:989aff5636d8f0213ed44fe334c3314a194a51c0c755d16866960247aafd2eae'
    & docker run --rm --network none --read-only --tmpfs /tmp --entrypoint python --mount __CHECKMOUNT__ --mount __WORKMOUNT__ --mount __DATAMOUNT__ $Image /check/nfi_runtime_preflight.py
    if ($LASTEXITCODE -ne 0) { throw 'Offline runtime configuration preflight failed. See runtime-preflight.json; no backtest started.' }
    & __PYTHON__ -m app.strategy_lab.nfi_source_reproduction --work __WORK__ --history __HISTORY__
    if ($LASTEXITCODE -ne 0) { throw 'History/source verification failed.' }
    & docker run --rm --read-only --tmpfs /tmp --entrypoint python --mount __CHECKMOUNT__ --mount __WORKMOUNT__ --mount __DATAMOUNT__ $Image /check/nfi_native_backtest.py
    if ($LASTEXITCODE -ne 0) { throw 'Native backtest did not produce a successful result. Keep native-backtest.log and native-run-status.json. No summary was attempted.' }
    & __PYTHON__ -m app.strategy_lab.nfi_source_reproduction --work __WORK__ --summarize
    if ($LASTEXITCODE -ne 0) { throw 'Native trade/result reconciliation failed.' }
    Write-Host __SUMMARY__
}
finally { Pop-Location }
'@
$Import = @'
$ErrorActionPreference = 'Stop'
Push-Location __SOURCE__
try {
    & __PYTHON__ -m app.strategy_lab.nfi_history_import --work __WORK__ --output __HISTORY__ --download
    if ($LASTEXITCODE -ne 0) { throw 'Pinned history import failed. Preserve history-import-summary.json; no backtest or bot was started.' }
    & __PYTHON__ -m app.strategy_lab.nfi_source_reproduction --work __WORK__ --history __HISTORY__
    if ($LASTEXITCODE -ne 0) { throw 'Original-universe history verification failed.' }
}
finally { Pop-Location }
Write-Host 'VERIFIED_PINNED_CANDLE_FILES; OLD_PUBLISHED_DATA_ID_NOT_ATTESTED'
'@
$NewsStart = @'
param([double]$Hours=1, [int]$MaxRequests=24)
$ErrorActionPreference = 'Stop'
Push-Location __SOURCE__
try {
    & __PYTHON__ -m app.asset_modules.grok_news_scout --repo __REPO__ --output __NEWS__ --hours $Hours --max-requests $MaxRequests --online
    if ($LASTEXITCODE -ne 0) { throw 'News/scout stopped. Native trading is independent; review latest status files.' }
}
finally { Pop-Location }
'@
$Scripts = @{'Import-Author-History.ps1'=$Import; 'Start-NFI-Backtest.ps1'=$Backtest; 'Start-Grok-News-Scout.ps1'=$NewsStart}
foreach ($Name in $Scripts.Keys) {
    $Text = $Scripts[$Name].Replace('__SOURCE__', (& $Quote $Source)).Replace('__PYTHON__', (& $Quote $Python)).Replace('__REPO__', (& $Quote $Repo)).Replace('__WORK__', (& $Quote $Work)).Replace('__HISTORY__', (& $Quote $History)).Replace('__NEWS__', (& $Quote $News)).Replace('__CHECKMOUNT__', (& $Quote ("type=bind,source=" + (Join-Path $Source 'app\strategy_lab') + ',target=/check,readonly'))).Replace('__WORKMOUNT__', (& $Quote "type=bind,source=$Work,target=/work")).Replace('__DATAMOUNT__', (& $Quote "type=bind,source=$History,target=/history,readonly")).Replace('__SUMMARY__', (& $Quote ("UPLOAD_SUMMARY=" + (Join-Path $Work 'reproduction-summary.json'))))
    Set-Content -LiteralPath (Join-Path $Root $Name) -Value $Text -Encoding UTF8
}
Write-Host "RESEARCH_DIRECTORY=$Root"
Write-Host "MANUAL_HISTORY=$(Join-Path $Root 'Import-Author-History.ps1')"
Write-Host "MANUAL_BACKTEST=$(Join-Path $Root 'Start-NFI-Backtest.ps1')"
Write-Host "MANUAL_NEWS_SCOUT=$(Join-Path $Root 'Start-Grok-News-Scout.ps1')"
Write-Host 'PREPARED_ONLY; NO_BOT_OR_GROK_STARTED; INSTALLED_ATLAS_AND_SETTINGS_UNCHANGED'
Write-Host 'Import author history, then run the NFI backtest. News/scout is an independent optional paid research process.'
