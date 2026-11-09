param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [string]$FleetRoot = (Join-Path $env:LOCALAPPDATA 'AtlasResearch\crypto-weekend-0db9d940ad9b48d68f0c2f3b5ded0ff5'),
    [string]$AddonRoot = (Join-Path $env:LOCALAPPDATA 'AtlasResearch\paper-candidate-addon-20b9aca30f94437b89b7a9c97cf0d631')
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Fleet = (Resolve-Path -LiteralPath (Join-Path $FleetRoot 'paper-fleet')).Path
$Addon = (Resolve-Path -LiteralPath (Join-Path $AddonRoot 'paper')).Path
$Image = 'freqtradeorg/freqtrade:2026.9@sha256:989aff5636d8f0213ed44fe334c3314a194a51c0c755d16866960247aafd2eae'
& docker image inspect $Image > $null
if ($LASTEXITCODE -ne 0) { throw 'Pinned Docker image unavailable.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\crypto-transport-repair-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
$Repair = Join-Path $Root 'repair'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Isolated checkout failed.' }
# Run failures/recovery, cache, native retry and database-preservation checks
# before stopping any crypto worker. Docker logs on stderr are normal output.
& docker run --rm --network none --read-only --tmpfs /tmp --workdir /check --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" $Image -m unittest tests.strategy_lab.test_kraken_public_transport tests.strategy_lab.test_crypto_transport_repair tests.strategy_lab.test_paper_candidate_addon -q
if ($LASTEXITCODE -ne 0) { throw 'Disconnected checks failed; existing workers unchanged.' }
Push-Location $Source
try {
    & $Python -m app.strategy_lab.crypto_transport_repair --fleet $Fleet --addon $Addon --source $Source --output $Repair
    if ($LASTEXITCODE -ne 0) { throw 'Preflight failed; existing workers unchanged.' }
    $PlanPath = Join-Path $Repair 'repair-plan.json'
    $Plan = Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
    $Running = @{}
    foreach ($Group in $Plan.groups) {
        & docker compose -f $Group.proposed config --quiet
        if ($LASTEXITCODE -ne 0) { throw 'Proposed Compose validation failed.' }
        foreach ($Service in $Group.services) {
            if (-not $Service.name) { throw 'Explicit existing container name required.' }
            $State = & docker inspect --format '{{.State.Running}}' $Service.name
            if ($LASTEXITCODE -ne 0) { throw "Container missing: $($Service.name)" }
            $Running[$Service.name] = ($State -eq 'true')
        }
    }
    $Running | ConvertTo-Json | Set-Content (Join-Path $Repair 'before-running.json') -Encoding UTF8
    foreach ($Group in $Plan.groups) {
        $Keys = @($Group.services | ForEach-Object { $_.key })
        & docker compose -f $Group.base stop -t 120 @Keys
        if ($LASTEXITCODE -ne 0) { throw 'Crypto cleanup failed; no source mounts replaced.' }
    }
    & $Python -m app.strategy_lab.crypto_transport_repair --output $Repair --apply
    if ($LASTEXITCODE -ne 0) { throw 'Repair not applied; retain repair directory. Crypto workers remain stopped.' }
    foreach ($Group in $Plan.groups) {
        $Keys = @($Group.services | Where-Object { $Running[$_.name] } | ForEach-Object { $_.key })
        if ($Keys.Count -gt 0) {
            & docker compose -f $Group.base up -d --no-deps --force-recreate @Keys
            if ($LASTEXITCODE -ne 0) { throw 'Crypto restart incomplete; retain repair directory and logs.' }
        }
    }
} finally { Pop-Location }
$Status = @'
$ErrorActionPreference = 'Stop'
$Plan = Get-Content -LiteralPath '__PLAN__' -Raw | ConvertFrom-Json
$Rows = foreach ($Group in $Plan.groups) {
    foreach ($Service in $Group.services) {
        $E = $null; $T = $null
        $Engine = Join-Path $Service.work 'engine-status.json'
        $Transport = Join-Path $Service.work 'transport-health.json'
        if (Test-Path $Engine) { $E = Get-Content $Engine -Raw | ConvertFrom-Json }
        if (Test-Path $Transport) { $T = Get-Content $Transport -Raw | ConvertFrom-Json }
        $Running = & docker inspect --format '{{.State.Running}}' $Service.name
        [pscustomobject]@{
            container = $Service.name
            container_running = $Running
            engine_state = $E.state
            engine_updated_at = $E.updated_at
            engine_age_seconds = $(if ($E.updated_at) { [math]::Round(([DateTimeOffset]::UtcNow - [DateTimeOffset]$E.updated_at).TotalSeconds,1) })
            fresh_ready_pairs = $E.fresh_ready_pairs
            transport_state = $T.state
            transport_updated_unix = $T.updated_at
            transport_stats = $T.stats
            last_network_success_age_seconds = $T.last_network_success_age_seconds
            capture_error = $E.capture_error
            pause_new_entries = $E.pause_new_entries
            source_readiness = $E.source_readiness.latest_counts
        }
    }
}
$Rows | ConvertTo-Json -Depth 8
'@
$StatusPath = Join-Path $Root 'Show-Crypto-Transport.ps1'
$Status.Replace('__PLAN__',$PlanPath.Replace("'","''")) | Set-Content -LiteralPath $StatusPath -Encoding UTF8
Write-Host "REPAIR_DIRECTORY=$Root"
Write-Host "READ_ONLY_STATUS=$StatusPath"
Write-Host 'CRYPTO_CODE_UPDATED; PRIOR_RUNNING_WORKERS_RESTART_REQUESTED; VERIFY_FRESH_DATA_WITH_STATUS'
Write-Host 'STRATEGIES_CONFIGS_WALLETS_AND_PAUSE_FLAGS_RETAINED; EQUITY_AND_ATLAS_UNCHANGED'
