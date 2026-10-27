param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'An exact reviewed commit is required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python is missing.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is outside the reviewed research branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\quote-source-probe-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
$Summary = Join-Path $Root 'summary.json'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Pinned probe checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/asset_modules/test_equity_quote_source_probe.py --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Probe verification failed. No data requested.' }
    & $Python -m app.webull.equity_quote_source_probe --repo $Repo --output $Summary --read
    if ($LASTEXITCODE -ne 0) { throw 'Quote-source probe setup failed. No trading settings changed.' }
}
finally {
    Pop-Location
    & git -C $Repo worktree remove --force $Source
}
if (Test-Path -LiteralPath $Summary -PathType Leaf) {
    Write-Host "UPLOAD_SUMMARY=$Summary"
    Get-Content -LiteralPath $Summary -Raw
}
