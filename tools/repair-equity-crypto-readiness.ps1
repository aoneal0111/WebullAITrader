param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$EquityTrial,
    [Parameter(Mandatory=$true)][string]$CryptoRoot,
    [switch]$RefreshCryptoUniverse
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$EquityTrial = (Resolve-Path -LiteralPath $EquityTrial).Path
$CryptoRoot = (Resolve-Path -LiteralPath $CryptoRoot).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Work = Join-Path $CryptoRoot 'paper'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python missing.' }
$CryptoId = (Split-Path -Leaf $CryptoRoot) -replace '^nfi-crypto-paper-', ''
if ($CryptoId -notmatch '^[0-9a-f]{32}$') { throw 'Original NFI paper directory required.' }
$Container = 'atlas-nfi-paper-' + $CryptoId
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|grok_day_proposals|bootstrap-observer\.py'
}
if ($Running) { throw 'Close Atlas and Grok observers normally before this repair.' }
$Containers = & docker ps --filter "name=^/$Container$" --format '{{.Names}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop unavailable.' }
if ($Containers -contains $Container) { throw 'Stop the existing NFI paper engine normally before this repair.' }
$Dirty = & git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Tracked changes detected. Nothing overwritten.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
& git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Repository requires integration. No reset attempted.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\equity-crypto-repair-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
$Image = 'freqtradeorg/freqtrade:2026.9@sha256:989aff5636d8f0213ed44fe334c3314a194a51c0c755d16866960247aafd2eae'
$PreviousQt = [Environment]::GetEnvironmentVariable('QT_QPA_PLATFORM','Process')
Push-Location $Source
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    & $Python -m pytest tests/test_authoritative_failure_recovery.py tests/test_p2_authoritative_lane.py tests/test_p2_live_failure_containment.py tests/test_shutdown_provenance.py tests/test_desktop_broker_runtime.py tests/test_performance_phase1.py tests/test_performance_phase2.py tests/test_performance_phase3.py tests/test_performance_phase4.py tests/test_performance_phase5.py tests/strategy_research/test_stopped_runtime_review.py tests/asset_modules/test_paper_morning_status.py tests/asset_modules/test_paper_day_setup.py tests/asset_modules/test_grok_warrior_control.py tests/equity_overhaul/test_account_paper_sizing.py tests/warrior_momentum/test_quick_scalper_runtime.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Equity regression checks failed. Installed source unchanged.' }
    & docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --workdir /check --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" $Image -m unittest tests.strategy_lab.test_nfi_forward_paper tests.strategy_lab.test_nfi_readiness -q
    if ($LASTEXITCODE -ne 0) { throw 'Native crypto regression checks failed. Installed source unchanged.' }
    & docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" --mount "type=bind,source=$Work,target=/work" $Image /check/app/strategy_lab/nfi_forward_engine.py --check-only
    if ($LASTEXITCODE -ne 0) { throw 'Existing NFI source/config verification failed. No wallet reset attempted.' }
    & $Python -m app.strategy_research.stopped_runtime_review --trial $EquityTrial --performance (Join-Path $env:TEMP 'atlas-performance') --output (Join-Path $Root 'equity-review')
    if ($LASTEXITCODE -ne 0) { throw 'Stopped equity evidence export failed. Installed source unchanged.' }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM',$PreviousQt,'Process')
    Pop-Location
}
$Backup = 'backup/before-equity-crypto-repair-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup branch failed.' }
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset attempted.' }
Push-Location $Repo
try {
    & $Python -m app.asset_modules.paper_day_setup --repo $Repo --output (Join-Path $Root 'equity-trial') --profile GROK_WARRIOR_ACCOUNT_V6 --extended-sessions
    if ($LASTEXITCODE -ne 0) { throw 'Fresh equity trial preparation failed. Nothing started.' }
} finally { Pop-Location }
$Quote = { param($Value) "'" + $Value.Replace("'", "''") + "'" }
$Token = [guid]::NewGuid().ToString('N')
$Refresh = if ($RefreshCryptoUniverse) { " --refresh-universe-once $Token" } else { '' }
$Start = @'
$ErrorActionPreference = 'Stop'
Write-Host 'NFI_KRAKEN_USD_PAPER; ORIGINAL_STRATEGY; EXISTING_WALLET_AND_LEDGER; NO_GROK; NO_REAL_ORDERS'
Write-Host 'PUBLIC_PREFLIGHT_AND_NATIVE_WARMUP_CAN_TAKE_SEVERAL_MINUTES; KEEP_THIS_TERMINAL_OPEN'
& docker run --rm --name __CONTAINER__ --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --entrypoint python --mount __SOURCE_MOUNT__ --mount __WORK_MOUNT__ __IMAGE__ /check/app/strategy_lab/nfi_forward_engine.py__REFRESH__
if ($LASTEXITCODE -ne 0) { throw 'NFI paper stopped with a diagnostic. Preserve native-paper.log; do not reset the ledger.' }
'@
$Status = @'
$ErrorActionPreference = 'Stop'
Push-Location __SOURCE__
try {
    & __PYTHON__ -m app.strategy_lab.nfi_forward_status --work __WORK__ --compact
    if ($LASTEXITCODE -ne 0) { throw 'NFI status failed.' }
} finally { Pop-Location }
'@
foreach ($Entry in @(
    @{Name='Start-NFI-Crypto-Repaired.ps1';Text=$Start},
    @{Name='Show-NFI-Crypto-Repaired.ps1';Text=$Status}
)) {
    $Text = $Entry.Text.Replace('__CONTAINER__',(& $Quote $Container)).Replace('__SOURCE_MOUNT__',(& $Quote "type=bind,source=$Source,target=/check,readonly")).Replace('__WORK_MOUNT__',(& $Quote "type=bind,source=$Work,target=/work")).Replace('__IMAGE__',(& $Quote $Image)).Replace('__REFRESH__',$Refresh).Replace('__SOURCE__',(& $Quote $Source)).Replace('__PYTHON__',(& $Quote $Python)).Replace('__WORK__',(& $Quote $Work))
    Set-Content -LiteralPath (Join-Path $Root $Entry.Name) -Value $Text -Encoding UTF8
}
Write-Host "UPLOAD_EQUITY_REVIEW=$(Join-Path $Root 'equity-review.zip')"
Write-Host "MANUAL_ATLAS=$(Join-Path $Root 'equity-trial\Start-Atlas-Paper-Trial.ps1')"
Write-Host "MANUAL_CRYPTO=$(Join-Path $Root 'Start-NFI-Crypto-Repaired.ps1')"
Write-Host "READ_ONLY_CRYPTO=$(Join-Path $Root 'Show-NFI-Crypto-Repaired.ps1')"
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host 'PREPARED_ONLY; NOTHING_STARTED; NO_API_REQUESTS; NO_ENV_FILE_CHANGED; CRYPTO_WALLET_AND_PRIOR_TRIALS_RETAINED'
Write-Host 'Original equity crash cause is not claimed fixed without its underlying exception. Preserve UPLOAD_EQUITY_REVIEW.'
