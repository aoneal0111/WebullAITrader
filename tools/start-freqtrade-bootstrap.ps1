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
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\lab-bootstrap-controller-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Controller checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/strategy_lab --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Bootstrap checks failed. Entry permission was not changed.' }
    & $Python -m app.strategy_lab.bootstrap --repo $Repo --lab $Lab --bootstrap --new-trial
    if ($LASTEXITCODE -ne 0) { throw 'Bootstrap stopped with an error. Keep the native dry-run bot running and send its status.' }
}
finally { Pop-Location }
Write-Host 'BOOTSTRAP_FINISHED; GROK_REQUESTS=0; ATLAS_BASELINE_UNCHANGED'
Write-Host "UPLOAD_SUMMARY=$(Join-Path $Lab 'bootstrap-summary.json')"
