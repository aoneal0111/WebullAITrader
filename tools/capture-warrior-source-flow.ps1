param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$PaperDirectory,
    [Parameter(Mandatory=$true)][string]$Review,
    [Parameter(Mandatory=$true)][string]$DiagnosticPath,
    [Parameter(Mandatory=$true)][string]$RunId,
    [Parameter(Mandatory=$true)][string]$DiagnosticSha256,
    [switch]$ApplyRepair
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$' -or $RunId -notmatch '^[0-9a-f]{32}$' -or $DiagnosticSha256 -notmatch '^[0-9A-Fa-f]{64}$') {
    throw 'Exact reviewed revision and diagnostic identity required.'
}
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$PaperDirectory = (Resolve-Path -LiteralPath $PaperDirectory).Path
$Review = (Resolve-Path -LiteralPath $Review).Path
$DiagnosticPath = (Resolve-Path -LiteralPath $DiagnosticPath).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|grok_day_proposals|bootstrap-observer\.py'
}
if ($Running) { throw 'Keep Atlas closed for the source-bound review.' }
if ($ApplyRepair) {
    $Dirty = & git -C $Repo status --porcelain --untracked-files=no
    if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Tracked changes detected. Nothing overwritten.' }
}
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
if ($ApplyRepair) {
    & git -C $Repo merge-base --is-ancestor HEAD $Revision
    if ($LASTEXITCODE -ne 0) { throw 'Repository requires integration. Nothing reset.' }
}
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\warrior-source-flow-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
$Out = Join-Path $Root 'warrior-source-flow.zip'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Isolated checkout failed.' }
$PreviousQt = [Environment]::GetEnvironmentVariable('QT_QPA_PLATFORM','Process')
Push-Location $Source
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    & $Python -c "import sys; from pathlib import Path; from app.equity_overhaul.paper_experiment import load, CONTINUOUS_SCOPE; assert load(Path(sys.argv[1]) / 'experiment.json', allow_expired=True)[1]['scope'] == CONTINUOUS_SCOPE" $PaperDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Existing continuous PAPER operating spec required.' }
    & $Python -m pytest tests/strategy_research/test_warrior_quote_flow_review.py tests/warrior_momentum/test_paper_stream_quote.py tests/warrior_momentum/test_execution_quote_confirmation.py tests/warrior_momentum/test_warrior_library_operation.py tests/composition/test_warrior_quote_routing.py tests/asset_modules/test_controlled_paper_service.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Quote-flow and entry checks failed. Installed source unchanged.' }
    & $Python -m app.strategy_research.warrior_quote_flow_review --review $Review --output $Out --diagnostic $DiagnosticPath --run-id $RunId --diagnostic-sha256 $DiagnosticSha256
    if ($LASTEXITCODE -ne 0) { throw 'Source verification failed. Preserve the error; do not rerun Atlas.' }
} finally {
    [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM',$PreviousQt,'Process')
    Pop-Location
}
if ($ApplyRepair) {
    $Backup = 'backup/before-warrior-source-flow-' + [guid]::NewGuid().ToString('N')
    & git -C $Repo branch $Backup HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
    & git -C $Repo merge --ff-only $Revision
    if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset attempted.' }
    Write-Host "BACKUP_BRANCH=$Backup"
    Write-Host "UPDATE_VERIFIED=$Revision"
}
Write-Host 'ATLAS_LEFT_STOPPED; NO_PROVIDER_OR_GROK_REQUESTS; NO_ORDERS; NO_ENV_OR_OPERATING_SPEC_CHANGED'
Write-Host 'WARRIOR_VOLUME_ONLY_SNAPSHOTS_ARE_NOT_FRESH_TRADES; SCALPER_AND_CRYPTO_UNCHANGED'
Write-Host "UPLOAD_FILE=$Out"
explorer.exe "/select,`"$Out`""
