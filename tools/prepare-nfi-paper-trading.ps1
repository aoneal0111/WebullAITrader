param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python missing.' }
& docker info --format '{{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop first. Nothing changed.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
$Id = [guid]::NewGuid().ToString('N')
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\nfi-crypto-paper-' + $Id)
$null = New-Item -ItemType Directory -Path $Root
$Review = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Review $Revision
if ($LASTEXITCODE -ne 0) { throw 'Research checkout failed.' }
$Nfi = Join-Path $Root 'nfi-source'
& git init $Nfi
if ($LASTEXITCODE -ne 0) { throw 'NFI source initialization failed.' }
& git -C $Nfi config core.autocrlf false
if ($LASTEXITCODE -ne 0) { throw 'Source byte preservation failed.' }
& git -C $Nfi config core.longpaths true
& git -C $Nfi fetch --depth 1 https://github.com/iterativv/NostalgiaForInfinity.git bbddbd94f97f3c14336387d2958183870891b951
if ($LASTEXITCODE -ne 0) { throw 'Pinned NFI fetch failed.' }
& git -C $Nfi checkout --detach FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Pinned NFI checkout failed.' }
$Work = Join-Path $Root 'paper'
$Image = 'freqtradeorg/freqtrade:2026.9@sha256:989aff5636d8f0213ed44fe334c3314a194a51c0c755d16866960247aafd2eae'
Push-Location $Review
try {
    # Source/config/status, capture and CCXT tests run inside the pinned container.
    & docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --workdir /check --entrypoint python --mount "type=bind,source=$Review,target=/check,readonly" $Image -m unittest tests.strategy_lab.test_nfi_forward_paper -q
    if ($LASTEXITCODE -ne 0) { throw 'Native paper integration tests failed; nothing started.' }
    & $Python -m app.strategy_lab.nfi_forward_paper --source $Nfi --work $Work --atlas-revision $Revision
    if ($LASTEXITCODE -ne 0) { throw 'Pinned source preparation failed.' }
    & docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --entrypoint python --mount "type=bind,source=$Review,target=/check,readonly" --mount "type=bind,source=$Work,target=/work" $Image /check/app/strategy_lab/nfi_forward_engine.py --check-only
    if ($LASTEXITCODE -ne 0) { throw 'Native container runtime check failed; no trading engine started.' }
}
finally { Pop-Location }
$Quote = { param($Value) "'" + $Value.Replace("'", "''") + "'" }
$Container = 'atlas-nfi-paper-' + $Id
$Start = @'
$ErrorActionPreference = 'Stop'
$Image = __IMAGE__
Write-Host 'NFI_KRAKEN_USD_PAPER; SEPARATE_10000_USD_WALLET; SOURCE_ENTRIES_EXITS_AND_ADJUSTMENTS; NO_GROK; NO_REAL_ORDERS'
Write-Host 'PUBLIC_PREFLIGHT_THEN_NATIVE_WARMUP; FIRST_DATA_LOAD_CAN_TAKE_SEVERAL_MINUTES; KEEP_THIS_WINDOW_OPEN'
Write-Host 'NFI_SOURCE_EMERGENCY_STOP_IS_MINUS_99_PERCENT; THIS_LAUNCHER_IS_PAPER_ONLY'
& docker run --rm --name __CONTAINER__ --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --entrypoint python --mount __SOURCE_MOUNT__ --mount __WORK_MOUNT__ $Image /check/app/strategy_lab/nfi_forward_engine.py
if ($LASTEXITCODE -ne 0) { throw 'NFI PAPER stopped with a diagnostic. Preserve native-paper.log and market-preflight.json; restart this same launcher to retain its wallet.' }
'@
$Status = @'
$ErrorActionPreference = 'Stop'
Push-Location __REVIEW__
try {
    & __PYTHON__ -m app.strategy_lab.nfi_forward_status --work __WORK__
    if ($LASTEXITCODE -ne 0) { throw 'Read-only NFI status failed.' }
} finally { Pop-Location }
'@
$Export = @'
$ErrorActionPreference = 'Stop'
$Destination = Join-Path __WORK__ ('review-' + [guid]::NewGuid().ToString('N'))
Push-Location __REVIEW__
try {
    & __PYTHON__ -m app.strategy_lab.nfi_forward_status --work __WORK__ --export $Destination
    if ($LASTEXITCODE -ne 0) { throw 'Native PAPER review export failed; originals retained.' }
} finally { Pop-Location }
'@
$Pause = @'
$ErrorActionPreference = 'Stop'
$null = New-Item -ItemType File -Path __PAUSE__ -Force
Write-Host 'NEW_NFI_ENTRIES_AND_ADDITIONS_PAUSED; NATIVE_EXITS_AND_POSITION_MANAGEMENT_CONTINUE'
'@
$Resume = @'
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath __PAUSE__) { Remove-Item -LiteralPath __PAUSE__ }
Write-Host 'OPERATOR_ENTRY_PAUSE_CLEARED; NATIVE_SOURCE_RULES_DATA_AND_CAPTURE_STILL_APPLY'
'@
$Stop = @'
$ErrorActionPreference = 'Stop'
& docker stop --time 60 __CONTAINER__
if ($LASTEXITCODE -ne 0) { throw 'Container stop failed. Check the NFI terminal.' }
Write-Host 'NFI_PAPER_STOPPED; WALLET_AND_DATABASE_RETAINED; OPEN_SIMULATED_POSITIONS_NEED_RESTART_FOR_MANAGEMENT'
'@
foreach ($Entry in @(
    @{Name='Start-NFI-Crypto-Paper.ps1'; Text=$Start},
    @{Name='Show-NFI-Crypto-Status.ps1'; Text=$Status},
    @{Name='Export-NFI-Paper-Review.ps1'; Text=$Export},
    @{Name='Pause-NFI-New-Entries.ps1'; Text=$Pause},
    @{Name='Resume-NFI-New-Entries.ps1'; Text=$Resume},
    @{Name='Stop-NFI-Crypto-Paper.ps1'; Text=$Stop}
)) {
    $Text = $Entry.Text.Replace('__IMAGE__', (& $Quote $Image)).Replace('__CONTAINER__', (& $Quote $Container)).Replace('__SOURCE_MOUNT__', (& $Quote "type=bind,source=$Review,target=/check,readonly")).Replace('__WORK_MOUNT__', (& $Quote "type=bind,source=$Work,target=/work")).Replace('__REVIEW__', (& $Quote $Review)).Replace('__WORK__', (& $Quote $Work)).Replace('__PYTHON__', (& $Quote $Python)).Replace('__PAUSE__', (& $Quote (Join-Path $Work 'PAUSE_NEW_ENTRIES')))
    Set-Content -LiteralPath (Join-Path $Root $Entry.Name) -Value $Text -Encoding UTF8
}
Write-Host "NFI_DIRECTORY=$Root"
Write-Host "MANUAL_CRYPTO=$(Join-Path $Root 'Start-NFI-Crypto-Paper.ps1')"
Write-Host "READ_ONLY_STATUS=$(Join-Path $Root 'Show-NFI-Crypto-Status.ps1')"
Write-Host "UPLOAD_SUMMARY=$(Join-Path $Work 'paper-manifest.json')"
Write-Host 'PREPARED_ONLY; NO_BOT_STARTED; NO_API_KEYS; NO_GROK_REQUESTS; EQUITY_TRIAL_AND_INSTALLED_ATLAS_UNCHANGED'
Write-Host 'Run MANUAL_CRYPTO in its own terminal. It is independent of Atlas START and its legacy Crypto module.'
