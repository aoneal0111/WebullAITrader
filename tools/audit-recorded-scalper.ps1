param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Session
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'An exact reviewed revision is required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Session = (Resolve-Path -LiteralPath $Session).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not on the reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\scalper-evidence-audit-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Audit checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/asset_modules/test_scalper_evidence_audit.py tests/warrior_momentum/test_quick_scalper.py tests/warrior_momentum/test_quick_scalper_runtime.py --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Offline audit verification failed. No settings changed.' }
    & $Python -m app.asset_modules.scalper_evidence_audit --session $Session --output (Join-Path $Root 'scalper-evidence-audit.json')
    if ($LASTEXITCODE -ne 0) { throw 'Evidence audit failed. Preserve the error; no recording or policy changed.' }
}
finally { Pop-Location }
Write-Host 'OFFLINE_ONLY; NO_GROK_OR_MARKET_DATA_REQUESTS; INSTALLED_ATLAS_UNCHANGED'
