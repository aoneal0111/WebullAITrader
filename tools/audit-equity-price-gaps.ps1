param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Study
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Study = (Resolve-Path -LiteralPath $Study).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not on the reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\equity-price-gap-audit-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
git -C $Repo worktree add --detach (Join-Path $Root 'review-source') $Revision
if ($LASTEXITCODE -ne 0) { throw 'Research checkout failed.' }
Push-Location (Join-Path $Root 'review-source')
try {
    & $Python -m pytest tests/strategy_research/test_episode_gap_audit.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Gap audit checks failed.' }
    & $Python -m app.strategy_research.episode_gap_audit --repo $Repo --study $Study --output (Join-Path $Root 'results')
    if ($LASTEXITCODE -ne 0) { throw 'Gap audit failed. Preserve the error. No source or settings changed.' }
} finally { Pop-Location }
Write-Host 'AUDIT_COMPLETE; NO_22GB_RESCAN; NO_API_REQUESTS; ATLAS_UNCHANGED'
