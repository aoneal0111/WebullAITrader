param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$ReviewDirectory,
    [Parameter(Mandatory=$true)][ValidateCount(1,10)][ValidatePattern('^[A-Z][A-Z0-9.\-]{0,14}$')][string[]]$Symbols
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$ReviewDirectory = (Resolve-Path -LiteralPath $ReviewDirectory).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Pointer = Get-Content -LiteralPath (Join-Path $ReviewDirectory 'recording-session.json') -Raw | ConvertFrom-Json
$Session = (Resolve-Path -LiteralPath $Pointer.session).Path
$Audit = Join-Path $ReviewDirectory 'atlas-equity-strategy-audit.json'
if (-not (Test-Path -LiteralPath $Audit)) { throw 'Closed review audit is missing.' }
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not on the reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\closed-exit-trace-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Read-only review checkout failed.' }
$Output = Join-Path $Root 'results'
Push-Location $Source
try {
    & $Python -m pytest tests/asset_modules/test_native_fill_funnel.py tests/strategy_research/test_exit_trace.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Review tests failed. Installed Atlas unchanged.' }
    Write-Host 'VERIFYING_CLOSED_JOURNAL_THEN_EXTRACTING_SELECTED_EXIT_WINDOWS; NO_API_REQUESTS'
    & $Python -m app.strategy_research.exit_trace --session $Session --audit $Audit --output $Output --symbols $Symbols
    if ($LASTEXITCODE -ne 0) { throw 'Closed evidence verification or extraction failed. Source preserved.' }
} finally { Pop-Location }
$Zip = Join-Path $Root 'closed-exit-trace.zip'
Compress-Archive -LiteralPath @((Join-Path $Output 'exit-trace-summary.json'), (Join-Path $Output 'private-exit-records.jsonl')) -DestinationPath $Zip
Write-Host "UPLOAD_FILE=$Zip"
Write-Host 'READ_ONLY; NO_TRADING_OR_GROK; INSTALLED_SOURCE_SETTINGS_AND_TRIAL_BUDGET_UNCHANGED'
Invoke-Item $Root
