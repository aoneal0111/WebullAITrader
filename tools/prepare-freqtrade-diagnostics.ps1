param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Lab
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'An exact reviewed revision is required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Lab = (Resolve-Path -LiteralPath $Lab).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python is missing.' }
if (-not (Test-Path -LiteralPath (Join-Path $Lab 'lab-manifest.json') -PathType Leaf)) { throw 'Prepared lab manifest is missing.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not in the reviewed research branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\lab-pipeline-diagnostics-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Diagnostic checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/strategy_lab --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Diagnostic checks failed. Nothing was started.' }
    $DiagnosticLab = Join-Path $Root 'experiment'
    & $Python -m app.strategy_lab.diagnostic_prepare --existing $Lab --output $DiagnosticLab
    if ($LASTEXITCODE -ne 0) { throw 'Diagnostic preparation failed. Nothing was started.' }
}
finally { Pop-Location }
$Quote = { param($Value) "'" + $Value.Replace("'", "''") + "'" }
$Project = 'atlas-nfi-diagnostic-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$Launcher = @'
$ErrorActionPreference = 'Stop'
$Compose = __COMPOSE__
$Project = __PROJECT__
$Lab = __LAB__
Push-Location __SOURCE__
try {
    & __PYTHON__ -m app.strategy_lab.supervisor --repo __REPO__ --lab $Lab --check
    if ($LASTEXITCODE -ne 0) { throw 'Diagnostic source/configuration mismatch.' }
}
finally { Pop-Location }
if (-not (Test-Path -LiteralPath (Join-Path $Lab 'user_data\DIAGNOSTICS_ENABLED'))) { throw 'Diagnostic entry-disable marker missing.' }
& docker compose -p $Project -f $Compose run --rm --entrypoint python atlas-nfi-lab /freqtrade/user_data/runtime_check.py
if ($LASTEXITCODE -ne 0) { throw 'Offline runtime check failed. Observation was not started.' }
$Timer = [Diagnostics.Stopwatch]::StartNew()
try {
    & docker compose -p $Project -f $Compose up -d
    if ($LASTEXITCODE -ne 0) { throw 'Diagnostic container startup failed.' }
    while ($Timer.Elapsed.TotalSeconds -lt 600) {
        Write-Host ("DIAGNOSTICS_RUNNING_SECONDS=" + [int]$Timer.Elapsed.TotalSeconds)
        Start-Sleep -Seconds 15
    }
}
finally {
    & docker compose -p $Project -f $Compose stop -t 30
}
$Result = Join-Path $Lab 'user_data\native-diagnostics.json'
if (-not (Test-Path -LiteralPath $Result)) {
    & docker compose -p $Project -f $Compose logs --tail 60
    throw 'No native diagnostic file was produced. Send these diagnostic container logs.'
}
Write-Host "UPLOAD_SUMMARY=$Result"
Get-Content -LiteralPath $Result -Raw
'@
$Launcher = $Launcher.Replace('__COMPOSE__', (& $Quote (Join-Path $DiagnosticLab 'compose.json'))).Replace('__PROJECT__', (& $Quote $Project)).Replace('__LAB__', (& $Quote $DiagnosticLab)).Replace('__SOURCE__', (& $Quote $Source)).Replace('__PYTHON__', (& $Quote $Python)).Replace('__REPO__', (& $Quote $Repo))
$StartScript = Join-Path $Root 'Start-Diagnostics.ps1'
Set-Content -LiteralPath $StartScript -Value $Launcher -Encoding UTF8
Write-Host "MANUAL_DIAGNOSTIC_START=$StartScript"
Write-Host 'PREPARED_ONLY; NO_BOT_STARTED; NO_GROK_REQUESTS; EXISTING_LAB_AND_ATLAS_UNCHANGED'
Write-Host 'Run the printed script manually: ten minutes of public Kraken data, no permitted entries. Only the separate diagnostic container is stopped afterward.'
