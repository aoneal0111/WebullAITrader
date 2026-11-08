param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [string]$FleetRoot = (Join-Path $env:LOCALAPPDATA 'AtlasResearch\crypto-weekend-0db9d940ad9b48d68f0c2f3b5ded0ff5')
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Fleet = (Resolve-Path (Join-Path $FleetRoot 'paper-fleet')).Path
$Work = Join-Path $Fleet 'bb'
$Compose = Join-Path $Fleet 'compose.json'
$Image = 'freqtradeorg/freqtrade:2026.9@sha256:989aff5636d8f0213ed44fe334c3314a194a51c0c755d16866960247aafd2eae'
& docker image inspect $Image > $null
if ($LASTEXITCODE -ne 0) { throw 'Pinned Docker image unavailable.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\bb-tag-repair-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
$Shadow = Join-Path $Root 'shadow'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
Push-Location $Source
try {
    & $Python -m app.strategy_lab.crypto_bb_repair --fleet $Fleet --shadow $Shadow
    if ($LASTEXITCODE -ne 0) { throw 'Existing BB verification failed; no changes applied.' }
    & docker run --rm --network none --read-only --tmpfs /tmp --workdir /check --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" --mount "type=bind,source=$Shadow,target=/work" $Image -m app.strategy_lab.crypto_fleet_runtime --work /work --check
    if ($LASTEXITCODE -ne 0) { throw 'Shadow native check failed; original BB unchanged.' }
    $Sources = Join-Path $FleetRoot 'upstream-cache'
    & docker run --rm --network none --read-only --tmpfs /tmp --workdir /check --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" --mount "type=bind,source=$Sources,target=/sources,readonly" -e ATLAS_FLEET_TEST_SOURCES=/sources $Image -m unittest tests.strategy_lab.test_crypto_parameter_checks tests.strategy_lab.test_crypto_bb_repair -q
    if ($LASTEXITCODE -ne 0) { throw 'Native BB regression failed; original BB unchanged.' }
    $Config = Get-Content $Compose -Raw | ConvertFrom-Json
    $Name = $Config.services.bb.container_name
    $State = & docker inspect --format '{{.State.Running}}' $Name 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'BB container not found; original fleet start required before repair.' }
    $WasRunning = ($State -eq 'true')
    $Pause = Join-Path $Work 'PAUSE_NEW_ENTRIES'
    $WasPaused = Test-Path -LiteralPath $Pause
    if (-not $WasPaused) { $null = New-Item -ItemType File -Path $Pause }
    if ($WasRunning) {
        & docker compose -f $Compose stop -t 120 bb
        if ($LASTEXITCODE -ne 0) { throw 'BB stop failed; entries remain paused.' }
    }
    & docker inspect --format '{{.State.Running}}' $Name | ForEach-Object { if ($_ -ne 'false') { throw 'BB must be stopped.' } }
    if ($LASTEXITCODE -ne 0) { throw 'Stopped-state verification failed.' }
    & docker logs --tail 1000 $Name *> (Join-Path $Root 'before-native.log')
    & $Python -m app.strategy_lab.crypto_bb_repair --fleet $Fleet --shadow $Shadow --source $Source
    if ($LASTEXITCODE -ne 0) { throw 'Code repair failed; BB left stopped and paused; retain repair folder.' }
    if ($WasRunning) {
        $Started = [DateTimeOffset]::UtcNow
        & docker compose -f $Compose up -d --no-deps --force-recreate bb
        if ($LASTEXITCODE -ne 0) { throw 'Repaired BB launch failed; entries remain paused.' }
        $Healthy = $false
        for ($I=0; $I -lt 12; $I++) {
            Start-Sleep -Seconds 5
            $StatusPath = Join-Path $Work 'engine-status.json'
            try {
                $E = Get-Content $StatusPath -Raw | ConvertFrom-Json
                $Healthy = ([DateTimeOffset]$E.updated_at -gt $Started -and $E.state -eq 'running' -and $E.fresh_ready_pairs -gt 0 -and $E.capture.writer_alive -and $E.capture.evidence_complete -and -not $E.capture_error -and -not $E.capture.capacity_reached)
            } catch { $Healthy = $false }
            if ($Healthy) { break }
        }
        if ($Healthy -and -not $WasPaused) { Remove-Item -LiteralPath $Pause }
        Write-Host "BB_RESTARTED; HEALTHY=$Healthy; PAUSED=$(Test-Path $Pause)"
    } else {
        if (-not $WasPaused) { Remove-Item -LiteralPath $Pause }
        Write-Host 'BB_REPAIRED_LEFT_STOPPED; START_USING_ORIGINAL_FLEET_LAUNCHER'
    }
    Write-Host "REPAIR_DIRECTORY=$Root"
    Write-Host 'BB_TAG_REPAIR_ONLY; SAME_WALLET_CONFIG_AND_HOLDINGS; OTHER_WORKERS_UNCHANGED; NO_LIVE_OR_GROK'
} finally { Pop-Location }
