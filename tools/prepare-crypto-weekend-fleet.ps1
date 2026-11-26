param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [string]$NfiRoot = (Join-Path $env:LOCALAPPDATA 'AtlasResearch\nfi-crypto-paper-7ed4d67e8fae44ce9be1e6ba6926a0cd')
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$NfiWork = (Resolve-Path -LiteralPath (Join-Path $NfiRoot 'paper')).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python missing.' }
$Image = 'freqtradeorg/freqtrade:2026.9@sha256:989aff5636d8f0213ed44fe334c3314a194a51c0c755d16866960247aafd2eae'
& docker image inspect $Image > $null
if ($LASTEXITCODE -ne 0) { throw 'Pinned native image missing or Docker Desktop unavailable.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\crypto-weekend-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
$Sources = Join-Path $Root 'upstream-cache'
$Fleet = Join-Path $Root 'paper-fleet'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Isolated review checkout failed.' }
Push-Location $Source
try {
    & $Python -m app.strategy_lab.crypto_fleet --root $Sources --fetch
    if ($LASTEXITCODE -ne 0) { throw 'Pinned public source download failed. Existing Atlas and NFI unchanged.' }
    & $Python -m app.strategy_lab.crypto_fleet --root $Fleet --sources $Sources --prepare --revision $Revision --nfi-work $NfiWork
    if ($LASTEXITCODE -ne 0) { throw 'Fleet preparation failed. No engine started.' }
    & docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --workdir /check --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" --mount "type=bind,source=$Sources,target=/sources,readonly" -e ATLAS_FLEET_TEST_SOURCES=/sources $Image -m unittest tests.strategy_lab.test_crypto_fleet tests.strategy_lab.test_nfi_forward_paper tests.strategy_lab.test_nfi_readiness -q
    if ($LASTEXITCODE -ne 0) { throw 'Disconnected native regressions failed. Nothing started.' }
    foreach ($Key in @('e0','nasos','bb','crsi','wave')) {
        $Work = Join-Path $Fleet $Key
        & docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --workdir /check --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" --mount "type=bind,source=$Work,target=/work" $Image -m app.strategy_lab.crypto_fleet_runtime --work /work --check
        if ($LASTEXITCODE -ne 0) { throw "Native profile check failed: $Key. Nothing started." }
    }
    & docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --workdir /check --entrypoint python --mount "type=bind,source=$Source,target=/check,readonly" --mount "type=bind,source=$NfiWork,target=/work" $Image -m app.strategy_lab.nfi_forward_engine --work /work --check-only
    if ($LASTEXITCODE -ne 0) { throw 'Original NFI source/config verification failed. Nothing started or reset.' }
} finally { Pop-Location }
$Quote = { param($Value) "'" + $Value.Replace("'", "''") + "'" }
$Start = @'
$ErrorActionPreference = 'Stop'
$Fleet = __FLEET__
$Manifest = Get-Content -LiteralPath (Join-Path $Fleet 'fleet.json') -Raw | ConvertFrom-Json
$Compose = Join-Path $Fleet 'compose.json'
foreach ($Key in @('e0','nasos','bb','crsi','wave')) {
    $Check = Get-Content -LiteralPath (Join-Path $Fleet "$Key\runtime-check.json") -Raw | ConvertFrom-Json
    if ($Check.status -ne 'RUNTIME_READY' -or $Check.live_trading_enabled) { throw "Profile not ready: $Key" }
    $Stop = Join-Path $Fleet "$Key\STOP"
    if (Test-Path -LiteralPath $Stop) { Remove-Item -LiteralPath $Stop }
}
Write-Host 'STARTING_FIVE_INDEPENDENT_PAPER_EXPERIMENTS; 10000_SIMULATED_USD_EACH; NO_GROK; NO_LIVE'
Write-Host 'Source adaptations are labeled; the published win rates are not assumed for these tests.'
& docker compose -f $Compose up -d e0 nasos bb crsi wave
if ($LASTEXITCODE -ne 0) { throw 'One or more candidate containers failed to launch. Run Show-Crypto-Weekend.ps1.' }
$NfiId = (Split-Path -Leaf (Split-Path -Parent $Manifest.nfi_work)) -replace '^nfi-crypto-paper-', ''
$NfiName = 'atlas-nfi-paper-' + $NfiId
$Existing = & docker ps --filter "name=^/$NfiName$" --format '{{.Names}}'
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect existing NFI process.' }
if ($Existing -contains $NfiName) {
    Write-Host 'NFI_ALREADY_RUNNING; EXISTING_WORKER_REUSED; NO_DUPLICATE_STARTED'
} else {
    $Stopped = & docker ps -a --filter "name=^/$NfiName$" --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect retained NFI container.' }
    if ($Stopped -contains $NfiName) {
        & docker start $NfiName
    } else {
        & docker compose -f $Compose up -d nfi
    }
    if ($LASTEXITCODE -ne 0) { throw 'NFI launch failed. Candidate status remains available separately.' }
}
Write-Host 'CONTAINERS_LAUNCHED; INITIAL_PUBLIC_CANDLE_WARMUP_CAN_TAKE_SEVERAL_MINUTES'
Write-Host 'Run Show-Crypto-Weekend.ps1 until each required worker is running with fresh data and healthy capture.'
Write-Host 'Docker runs detached. Keep the PC awake and Docker Desktop running. No eight-hour expiry or daily trade quota.'
& __SHOW__
'@
$Show = @'
$ErrorActionPreference = 'Stop'
Push-Location __SOURCE__
try {
    $Raw = & __PYTHON__ -m app.strategy_lab.crypto_fleet --root __FLEET__ --status
    if ($LASTEXITCODE -ne 0) { throw 'Read-only fleet status failed.' }
    $Report = $Raw | ConvertFrom-Json
    $Manifest = Get-Content -LiteralPath (Join-Path __FLEET__ 'fleet.json') -Raw | ConvertFrom-Json
    foreach ($Property in $Report.profiles.PSObject.Properties) {
        $Key = $Property.Name
        if ($Key -eq 'nfi') {
            $Id = (Split-Path -Leaf (Split-Path -Parent $Manifest.nfi_work)) -replace '^nfi-crypto-paper-', ''
            $Name = 'atlas-nfi-paper-' + $Id
        } else { $Name = 'atlas-crypto-' + $Manifest.id + '-' + $Key }
        $State = & docker ps -a --filter "name=^/$Name$" --format '{{.Status}}'
        $Property.Value | Add-Member -NotePropertyName container_status -NotePropertyValue (($State -join '') -replace '^$','NOT_CREATED')
    }
    $Report | ConvertTo-Json -Depth 12
} finally { Pop-Location }
'@
$Pause = @'
$ErrorActionPreference = 'Stop'
$Fleet = __FLEET__
foreach ($Key in @('e0','nasos','bb','crsi','wave')) {
    Set-Content -LiteralPath (Join-Path $Fleet "$Key\PAUSE_NEW_ENTRIES") -Value 'OPERATOR_PAUSE'
}
$Manifest = Get-Content -LiteralPath (Join-Path $Fleet 'fleet.json') -Raw | ConvertFrom-Json
Set-Content -LiteralPath (Join-Path $Manifest.nfi_work 'PAUSE_NEW_ENTRIES') -Value 'OPERATOR_PAUSE'
Write-Host 'NEW_ENTRIES_PAUSED; NATIVE_EXIT_MANAGEMENT_CONTINUES'
'@
$Resume = @'
$ErrorActionPreference = 'Stop'
$Fleet = __FLEET__
$Manifest = Get-Content -LiteralPath (Join-Path $Fleet 'fleet.json') -Raw | ConvertFrom-Json
$Paths = @('e0','nasos','bb','crsi','wave') | ForEach-Object { Join-Path $Fleet "$_\PAUSE_NEW_ENTRIES" }
$Paths += Join-Path $Manifest.nfi_work 'PAUSE_NEW_ENTRIES'
foreach ($Path in $Paths) { if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path } }
Write-Host 'OPERATOR_ENTRY_PAUSE_REMOVED; NORMAL_NATIVE_ENTRY_CONDITIONS_APPLY'
'@
$Stop = @'
$ErrorActionPreference = 'Stop'
& __PAUSE__
& docker compose -f (Join-Path __FLEET__ 'compose.json') stop -t 120 e0 nasos bb crsi wave
if ($LASTEXITCODE -ne 0) { throw 'Candidate cleanup incomplete. Preserve status and logs.' }
$Manifest = Get-Content -LiteralPath (Join-Path __FLEET__ 'fleet.json') -Raw | ConvertFrom-Json
$Id = (Split-Path -Leaf (Split-Path -Parent $Manifest.nfi_work)) -replace '^nfi-crypto-paper-', ''
$Name = 'atlas-nfi-paper-' + $Id
$Running = & docker ps --filter "name=^/$Name$" --format '{{.Names}}'
if ($Running -contains $Name) {
    & docker stop --time 120 $Name
    if ($LASTEXITCODE -ne 0) { throw 'NFI cleanup incomplete.' }
}
Write-Host 'WORKERS_STOPPED; LEDGERS_PRESERVED; THIS_DOES_NOT_FLATTEN_POSITIONS'
Write-Host 'Restart the same launchers to manage any remaining simulated holdings. Entry pauses remain set.'
'@
$Export = @'
$ErrorActionPreference = 'Stop'
Push-Location __SOURCE__
try {
    & __PYTHON__ -m app.strategy_lab.crypto_fleet --root __FLEET__ --export
    if ($LASTEXITCODE -ne 0) { throw 'Review export failed.' }
    Invoke-Item __FLEET__
} finally { Pop-Location }
'@
foreach ($Entry in @(
    @{Name='Start-Crypto-Weekend.ps1';Text=$Start},
    @{Name='Show-Crypto-Weekend.ps1';Text=$Show},
    @{Name='Pause-Crypto-New-Entries.ps1';Text=$Pause},
    @{Name='Resume-Crypto-New-Entries.ps1';Text=$Resume},
    @{Name='Stop-Crypto-Weekend.ps1';Text=$Stop},
    @{Name='Export-Crypto-Weekend.ps1';Text=$Export}
)) {
    $Text = $Entry.Text.Replace('__FLEET__',(& $Quote $Fleet)).Replace('__SOURCE__',(& $Quote $Source)).Replace('__PYTHON__',(& $Quote $Python)).Replace('__SHOW__',(& $Quote (Join-Path $Root 'Show-Crypto-Weekend.ps1'))).Replace('__PAUSE__',(& $Quote (Join-Path $Root 'Pause-Crypto-New-Entries.ps1')))
    Set-Content -LiteralPath (Join-Path $Root $Entry.Name) -Value $Text -Encoding UTF8
}
& docker compose -f (Join-Path $Fleet 'compose.json') config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Generated compose validation failed. Nothing started.' }
Write-Host "WEEKEND_DIRECTORY=$Root"
Write-Host "MANUAL_START=$(Join-Path $Root 'Start-Crypto-Weekend.ps1')"
Write-Host "READ_ONLY_STATUS=$(Join-Path $Root 'Show-Crypto-Weekend.ps1')"
Write-Host "EXPORT_REVIEW=$(Join-Path $Root 'Export-Crypto-Weekend.ps1')"
Write-Host 'PREPARED_ONLY; NO_WORKER_OR_ATLAS_STARTED; NO_MARKET_DATA_OR_GROK_REQUESTS'
Write-Host 'INSTALLED_ATLAS_ENV_AND_EQUITY_TRIAL_UNCHANGED; EXISTING_NFI_WALLET_RETAINED'
Write-Host 'Run MANUAL_START yourself. The five new wallets are independent experiments, not a shared 50000 USD trading account.'
Invoke-Item $Root
