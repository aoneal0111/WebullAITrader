param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Screen
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Screen = (Resolve-Path -LiteralPath $Screen).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not on the research branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\crypto-cost-audit-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Source checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/strategy_lab/test_cost_attribution.py --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Accounting verification failed.' }
    $Output = Join-Path $Root 'cost-attribution.json'
    & $Python -m app.strategy_lab.cost_attribution --screen $Screen --output $Output
    if ($LASTEXITCODE -ne 0) { throw 'Audit failed; no trading settings changed.' }
    Write-Host "UPLOAD_SUMMARY=$Output"
}
finally { Pop-Location }
