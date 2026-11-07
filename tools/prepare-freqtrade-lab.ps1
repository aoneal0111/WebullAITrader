param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'An exact reviewed revision is required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python is missing.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not in the reviewed research branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\freqtrade-lab-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'integration-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Integration checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/strategy_lab --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Lab checks failed. Nothing was started.' }
    $Lock = Get-Content -LiteralPath 'app\strategy_lab\source-lock.json' -Raw | ConvertFrom-Json
    $Upstream = Join-Path $Root 'pinned-upstream'
    & git init $Upstream
    if ($LASTEXITCODE -ne 0) { throw 'Upstream checkout initialization failed.' }
    & git -C $Upstream config core.autocrlf false
    if ($LASTEXITCODE -ne 0) { throw 'Pinned source byte-preservation setup failed.' }
    & git -C $Upstream fetch --depth 1 $Lock.nfi_repository $Lock.nfi_commit
    if ($LASTEXITCODE -ne 0) { throw 'Pinned NFI source download failed.' }
    & git -C $Upstream checkout --detach FETCH_HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Pinned NFI checkout failed.' }
    $Actual = & git -C $Upstream rev-parse HEAD
    if ($Actual -ne $Lock.nfi_commit) { throw 'Pinned NFI commit mismatch.' }
    $Lab = Join-Path $Root 'experiment'
    & $Python -m app.strategy_lab.prepare --source $Upstream --output $Lab
    if ($LASTEXITCODE -ne 0) { throw 'Lab generation failed.' }
}
finally { Pop-Location }
$Quote = { param($Value) "'" + $Value.Replace("'", "''") + "'" }
$Check = @'
$ErrorActionPreference = 'Stop'
Push-Location __SOURCE__
try {
    & __PYTHON__ -m app.strategy_lab.supervisor --repo __REPO__ --lab __LAB__ --check
    if ($LASTEXITCODE -ne 0) { throw 'Lab source/configuration verification failed.' }
    & __PYTHON__ -m app.strategy_lab.supervisor --repo __REPO__ --lab __LAB__ --pause
    if ($LASTEXITCODE -ne 0) { throw 'Could not pause lab entries before startup.' }
}
finally { Pop-Location }
'@
$Start = $Check + @'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker Desktop with Linux containers is required. Nothing was started.' }
$Kind = & docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0 -or $Kind -ne 'linux') { throw 'Docker Linux engine is not ready. Nothing was started.' }
& docker compose -p __PROJECT__ -f __COMPOSE__ run --rm --entrypoint python atlas-nfi-lab /freqtrade/user_data/runtime_check.py
if ($LASTEXITCODE -ne 0) { throw 'Pinned Freqtrade runtime check failed. The trading loop was not started.' }
& docker compose -p __PROJECT__ -f __COMPOSE__ up
if ($LASTEXITCODE -ne 0) { throw 'Dry-run container stopped with an error. Send the final error lines; no credentials.' }
'@
$Manager = @'
$ErrorActionPreference = 'Stop'
Push-Location __SOURCE__
try {
    & __PYTHON__ -m app.strategy_lab.supervisor --repo __REPO__ --lab __LAB__ --online --new-trial
    if ($LASTEXITCODE -ne 0) { throw 'Lab manager stopped with an error. Entries expire automatically; native exits remain with the dry-run bot.' }
}
finally { Pop-Location }
'@
$Pause = $Manager.Replace('--online --new-trial', '--pause')
$Scripts = @{'Start-DryRun.ps1'=$Start; 'Start-Grok-Manager.ps1'=$Manager; 'Pause-Entries.ps1'=$Pause}
$Project = 'atlas-nfi-lab-' + ([guid]::NewGuid().ToString('N').Substring(0,12))
foreach ($Name in $Scripts.Keys) {
    $Text = $Scripts[$Name].Replace('__SOURCE__', (& $Quote $Source)).Replace('__PYTHON__', (& $Quote $Python)).Replace('__REPO__', (& $Quote $Repo)).Replace('__LAB__', (& $Quote $Lab)).Replace('__COMPOSE__', (& $Quote (Join-Path $Lab 'compose.json'))).Replace('__PROJECT__', (& $Quote $Project))
    Set-Content -LiteralPath (Join-Path $Root $Name) -Value $Text -Encoding UTF8
}
Write-Host "LAB_DIRECTORY=$Lab"
Write-Host "MANUAL_DRY_RUN=$(Join-Path $Root 'Start-DryRun.ps1')"
Write-Host "MANUAL_GROK_MANAGER=$(Join-Path $Root 'Start-Grok-Manager.ps1')"
Write-Host "MANUAL_PAUSE_ENTRIES=$(Join-Path $Root 'Pause-Entries.ps1')"
Write-Host 'PREPARED_ONLY; NO_BOT_STARTED; NO_GROK_REQUESTS; NO_ATLAS_SETTINGS_CHANGED'
Write-Host 'Start the dry-run bot manually first, then the manager in a second shell.'
Write-Host 'Grok manager: up to four paid requests over about 15 minutes. It pauses entries when it finishes.'
Write-Host 'Keep the dry-run bot running for native protection of any virtual positions. This is a separate crypto experiment, not restored Atlas equity trading.'
