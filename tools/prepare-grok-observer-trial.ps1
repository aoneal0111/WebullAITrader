param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'An exact reviewed commit is required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python is missing.' }
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and
    $_.CommandLine -match 'app\.gui\.app|bootstrap-observer\.py'
}
if ($Running) { throw 'Close Atlas normally before preparing the new frozen capture.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not in the reviewed research branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\grok-observer-trial-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'observer-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Pinned observer source checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/asset_modules/test_grok_online_observer.py tests/asset_modules/test_grok_request_diagnostics.py tests/asset_modules/test_grok_decision_council.py tests/asset_modules/test_grok_observer_session.py --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Observer checks failed. Nothing was launched.' }
    $Capture = Join-Path $Root 'capture'
    & $Python -m app.asset_modules.grok_observer_session --repo $Repo --output $Capture
    if ($LASTEXITCODE -ne 0) { throw 'Frozen capture preparation failed. Nothing was launched.' }
}
finally { Pop-Location }
$Launch = @'
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Manifest = Join-Path $Root 'capture\observer-manifest.json'
if (-not (Test-Path -LiteralPath $Manifest -PathType Leaf)) {
    throw 'First launch Atlas with capture\Start-Observer-Capture.ps1, then click normal START.'
}
Push-Location (Join-Path $Root 'observer-source')
try {
    & __PYTHON__ -m app.asset_modules.grok_online_observer --repo __REPO__ `
        --capture (Join-Path $Root 'capture\private-capture\private-observer-context.jsonl') `
        --baseline-manifest $Manifest --output (Join-Path $Root 'online-results') --online
    if ($LASTEXITCODE -ne 0) { throw 'Observer stopped with a diagnostic status. Share online-results\summary.json only if present.' }
}
finally { Pop-Location }
'@
$Quote = { param($Value) "'" + $Value.Replace("'", "''") + "'" }
$Launch = $Launch.Replace('__PYTHON__', (& $Quote $Python)).Replace('__REPO__', (& $Quote $Repo))
Set-Content -LiteralPath (Join-Path $Root 'Start-Grok-Observer.ps1') -Value $Launch -Encoding UTF8
Write-Host "TRIAL_DIRECTORY=$Root"
Write-Host "MANUAL_ATLAS_CAPTURE=$(Join-Path $Capture 'Start-Observer-Capture.ps1')"
Write-Host "MANUAL_GROK_OBSERVER=$(Join-Path $Root 'Start-Grok-Observer.ps1')"
Write-Host 'PREPARED_ONLY; NO_GUI_OR_API_REQUESTS; INSTALLED_SOURCE_AND_SETTINGS_UNCHANGED'
Write-Host 'Run the Atlas capture manually first, click START, then run the Grok observer in a separate PowerShell window.'
Write-Host 'Observer: up to 20 paid requests, five active minutes, 15-minute startup wait; no order dispatch or automatic policy changes.'
