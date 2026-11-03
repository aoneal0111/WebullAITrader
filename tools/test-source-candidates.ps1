param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [decimal]$FixedResearchCost = 0
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$' -or $FixedResearchCost -lt 0) { throw 'Exact revision and nonnegative research cost required.' }
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\source-candidate-lab-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Root | Out-Null
$Source = Join-Path $Root 'review-source'
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/strategy_research/test_source_candidate_lab.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Candidate checks failed.' }
    & $Python -m app.strategy_research.source_candidate_capture --repo $Repo --output (Join-Path $Root 'history')
    if ($LASTEXITCODE -ne 0) { throw 'Capture incomplete. Preserve capture-summary.json. No feed fallback or subscription attempted.' }
    & $Python -m app.strategy_research.source_candidate_lab --daily-csv (Join-Path $Root 'history\daily.csv') --evaluation-start '2023-01-01' --fixed-overhead $FixedResearchCost --output (Join-Path $Root 'comparison.json')
    if ($LASTEXITCODE -ne 0) { throw 'Offline candidate comparison failed.' }
} finally { Pop-Location }
Write-Host "UPLOAD_SUMMARY=$(Join-Path $Root 'comparison.json')"
Write-Host 'OFFLINE_CANDIDATES_COMPLETE; INSTALLED_ATLAS_UNCHANGED; NO_GROK_OR_ORDER_REQUESTS; PROMOTION=NONE'
Invoke-Item $Root
