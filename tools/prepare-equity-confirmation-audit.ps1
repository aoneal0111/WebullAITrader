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
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\equity-confirmation-audit-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'audit-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Pinned audit checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/asset_modules/test_grok_observer_runtime_tap.py tests/asset_modules/test_grok_observer_session.py tests/warrior_momentum/test_execution_quote_confirmation.py --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Passive audit checks failed. Nothing was launched.' }
    $Capture = Join-Path $Root 'capture'
    & $Python -m app.asset_modules.grok_observer_session --repo $Repo --output $Capture
    if ($LASTEXITCODE -ne 0) { throw 'Frozen capture preparation failed. Nothing was launched.' }
}
finally { Pop-Location }
Write-Host "AUDIT_DIRECTORY=$Root"
Write-Host "MANUAL_ATLAS_CAPTURE=$(Join-Path $Capture 'Start-Observer-Capture.ps1')"
Write-Host "RESULT_SUMMARY=$(Join-Path $Capture 'session-summary.json')"
Write-Host 'PREPARED_ONLY; ATLAS_NOT_STARTED; NO_GROK_REQUESTS; INSTALLED_BASELINE_UNCHANGED'
Write-Host 'Launch the printed capture script manually, then click START normally. Capture lasts five active minutes.'
Write-Host 'Do not run a Grok observer for this audit. Existing native confirmation calls are compared; no extra quote requests.'
