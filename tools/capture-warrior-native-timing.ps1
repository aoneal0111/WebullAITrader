param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$PaperDirectory
)
# Atlas may remain running. No installed source, trading state or settings changed.
$ErrorActionPreference='Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo=(Resolve-Path -LiteralPath $Repo).Path
$Python=Join-Path $Repo '.venv\Scripts\python.exe'
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
$Root=Join-Path $env:LOCALAPPDATA ('AtlasResearch\warrior-native-timing-' + [guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $Root
$Source=Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Read-only review checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/strategy_research/test_warrior_native_timing_review.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Timing capture checks failed.' }
    $Out=Join-Path $Root 'warrior-native-timing.json'
    & $Python -m app.strategy_research.warrior_native_timing_review --repo $Repo --paper $PaperDirectory --output $Out
    if ($LASTEXITCODE -ne 0) { throw 'Timing capture failed. Atlas unchanged.' }
} finally { Pop-Location }
Write-Host 'READ_ONLY; ATLAS_NOT_STOPPED_OR_STARTED; NO_PROVIDER_OR_MODEL_REQUESTS; NO_INSTALLED_SOURCE_CHANGED'
explorer.exe "/select,`"$Out`""
