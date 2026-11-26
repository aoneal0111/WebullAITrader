param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [switch]$StartAfterPreparation
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Image = 'freqtradeorg/freqtrade:2026.9@sha256:989aff5636d8f0213ed44fe334c3314a194a51c0c755d16866960247aafd2eae'
& docker image inspect $Image > $null
if ($LASTEXITCODE -ne 0) { throw 'Pinned image missing or Docker Desktop unavailable.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\paper-candidate-addon-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
$Paper = Join-Path $Root 'paper'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Isolated checkout failed.' }
& docker run --rm --network none --read-only --tmpfs /tmp --workdir /check --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" $Image -m unittest tests.strategy_lab.test_paper_candidate_addon tests.strategy_lab.test_broad_strategy_lab -q
if ($LASTEXITCODE -ne 0) { throw 'Disconnected candidate tests failed. Nothing started.' }
Push-Location $Source
try {
    & $Python -m app.strategy_lab.paper_candidate_addon --root $Paper --prepare --revision $Revision
    if ($LASTEXITCODE -ne 0) { throw 'Preparation failed.' }
} finally { Pop-Location }
$Work = Join-Path $Paper 'crypto'
& docker run --rm --network none --read-only --tmpfs /tmp --workdir /check --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" --mount "type=bind,source=$Work,target=/work" $Image -m app.strategy_lab.crypto_fleet_runtime --work /work --check
if ($LASTEXITCODE -ne 0) { throw 'Native crypto runtime check failed. Nothing started.' }
$Q = { param($Value) "'" + $Value.Replace("'", "''") + "'" }
$Prefix = '$ErrorActionPreference = ''Stop''' + "`n" + '$Paper = ' + (& $Q $Paper) + "`n" + '$Compose = Join-Path $Paper ''compose.json''' + "`n"
$Start = @'
foreach ($Key in @('crypto','equity')) {
    $Stop = Join-Path $Paper "$Key\STOP"
    if (Test-Path -LiteralPath $Stop) { Remove-Item -LiteralPath $Stop }
}
$Check = Get-Content (Join-Path $Paper 'crypto\runtime-check.json') -Raw | ConvertFrom-Json
if ($Check.status -ne 'RUNTIME_READY') { throw 'Crypto runtime not ready.' }
Write-Host 'CHECKING_CURRENT_PUBLIC_FEEDS; NO_ORDER_REQUESTS'
& docker run --rm --read-only --tmpfs /tmp --workdir /check --entrypoint python --mount __CHECK_MOUNT__ --mount __PAPER_MOUNT__ __IMAGE__ -m app.strategy_lab.paper_candidate_addon --root /work --public-check
if ($LASTEXITCODE -ne 0) { throw 'Public readiness check failed.' }
$Public = Get-Content (Join-Path $Paper 'public-preflight.json') -Raw | ConvertFrom-Json
foreach ($Key in @('crypto','equity')) {
    $Ready = $Public.workers.$Key
    if ($Ready.ready) {
        & docker compose -f $Compose up -d $Key
        if ($LASTEXITCODE -ne 0) { throw "Add-on launch failed: $Key" }
        Write-Host "CONTAINER_LAUNCHED=$Key"
    } else { Write-Host "WORKER_NOT_STARTED=$Key; REASON=$($Ready.reason)" }
}
Write-Host 'ADDON_START_CHECK_COMPLETE; SEE_PER_WORKER_STATUS; ORIGINAL_NFI_AND_FLEET_UNCHANGED'
Write-Host 'Equity fills are fresh public-price simulations, not broker orders. Crypto uses native Freqtrade dry-run.'
'@
$Show = @'
& docker compose -f $Compose ps -a
if ($LASTEXITCODE -ne 0) { throw 'Container status unavailable.' }
'@
$Show += "`nPush-Location " + (& $Q $Source) + "`ntry {`n    & " + (& $Q $Python) + " -m app.strategy_lab.paper_candidate_addon --root `"`$Paper`"`n    if (`$LASTEXITCODE -ne 0) { throw 'Status failed.' }`n} finally { Pop-Location }`n"
$Pause = @'
foreach ($Key in @('crypto','equity')) { Set-Content (Join-Path $Paper "$Key\PAUSE_NEW_ENTRIES") 'OPERATOR_PAUSE' }
Write-Host 'ADDON_ENTRIES_PAUSED; RUNNING_EXITS_CONTINUE'
'@
$Resume = @'
foreach ($Key in @('crypto','equity')) {
    $Path = Join-Path $Paper "$Key\PAUSE_NEW_ENTRIES"
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path }
}
Write-Host 'ADDON_OPERATOR_PAUSE_REMOVED; SOURCE_RULES_AND_FRESH_DATA_STILL_REQUIRED'
'@
$Stop = @'
foreach ($Key in @('crypto','equity')) {
    Set-Content (Join-Path $Paper "$Key\PAUSE_NEW_ENTRIES") 'OPERATOR_PAUSE'
    Set-Content (Join-Path $Paper "$Key\STOP") 'OPERATOR_STOP'
}
& docker compose -f $Compose stop -t 120 crypto equity
if ($LASTEXITCODE -ne 0) { throw 'Cleanup incomplete. Preserve logs.' }
Write-Host 'ADDONS_STOPPED; HOLDINGS_RETAINED_NOT_FLATTENED; OLD_FLEET_AND_NFI_UNCHANGED'
'@
$Export = "Push-Location " + (& $Q $Source) + "`ntry {`n    & " + (& $Q $Python) + " -m app.strategy_lab.paper_candidate_addon --root `"`$Paper`" --export`n    if (`$LASTEXITCODE -ne 0) { throw 'Export failed.' }`n} finally { Pop-Location }`n"
foreach ($Entry in @(
    @{Name='Start-Paper-Candidates.ps1';Text=$Start},
    @{Name='Show-Paper-Candidates.ps1';Text=$Show},
    @{Name='Pause-Paper-Candidates.ps1';Text=$Pause},
    @{Name='Resume-Paper-Candidates.ps1';Text=$Resume},
    @{Name='Stop-Paper-Candidates.ps1';Text=$Stop},
    @{Name='Export-Paper-Candidates.ps1';Text=$Export}
)) {
    $Text = $Entry.Text.Replace('__CHECK_MOUNT__',(& $Q "type=bind,source=$Source,target=/check,readonly")).Replace('__PAPER_MOUNT__',(& $Q "type=bind,source=$Paper,target=/work")).Replace('__IMAGE__',(& $Q $Image))
    Set-Content (Join-Path $Root $Entry.Name) ($Prefix + $Text) -Encoding UTF8
}
& docker compose -f (Join-Path $Paper 'compose.json') config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose validation failed.' }
Write-Host "ADDON_DIRECTORY=$Root"
Write-Host "MANUAL_START=$(Join-Path $Root 'Start-Paper-Candidates.ps1')"
Write-Host "READ_ONLY_STATUS=$(Join-Path $Root 'Show-Paper-Candidates.ps1')"
Write-Host 'PREPARATION_COMPLETE; ORIGINAL_WALLETS_AND_ENV_UNCHANGED'
if ($StartAfterPreparation) {
    & (Join-Path $Root 'Start-Paper-Candidates.ps1')
    & (Join-Path $Root 'Show-Paper-Candidates.ps1')
} else { Write-Host 'PREPARED_ONLY; NOTHING_STARTED' }
