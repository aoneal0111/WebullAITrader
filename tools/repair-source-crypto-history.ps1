param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$ResearchDirectory
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$ResearchDirectory = (Resolve-Path -LiteralPath $ResearchDirectory).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Work = Join-Path $ResearchDirectory 'backtest'
$Original = Join-Path $ResearchDirectory 'Start-NFI-Backtest.ps1'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python missing.' }
if (-not (Test-Path -LiteralPath $Original -PathType Leaf)) { throw 'Original backtest launcher missing.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is outside reviewed branch.' }
$Repair = Join-Path $ResearchDirectory ('history-repair-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Repair
$Source = Join-Path $Repair 'review-source'
$History = Join-Path $Repair 'pinned-author-history'
$Checks = Join-Path $env:TEMP ('atlas-history-checks-' + [guid]::NewGuid().ToString('N').Substring(0,12))
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Repair checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/strategy_lab/test_nfi_history_import.py tests/strategy_lab/test_nfi_source_reproduction.py -q --basetemp $Checks
    if ($LASTEXITCODE -ne 0) { throw 'History importer checks failed. Nothing downloaded.' }
    Write-Host "REPAIR_DIRECTORY=$Repair"
    Write-Host 'PINNED_CANDLE_DOWNLOAD=800_FILES; EXPECTED_BYTES=2496224192; ORIGINAL_IMPORT_PRESERVED'
    & $Python -m app.strategy_lab.nfi_history_import --work $Work --output $History --download
    if ($LASTEXITCODE -ne 0) { throw 'Pinned history import failed. Preserve the printed directory and history-import-summary.json.' }
    & $Python -m app.strategy_lab.nfi_source_reproduction --work $Work --history $History
    if ($LASTEXITCODE -ne 0) { throw 'Complete original-universe inventory failed; no backtest was started.' }
}
finally { Pop-Location }
$OldHistory = Join-Path $ResearchDirectory 'author-history'
$Text = Get-Content -LiteralPath $Original -Raw
$HistoryReferences = ([regex]::Matches($Text, [regex]::Escape($OldHistory))).Count
if ($HistoryReferences -notin @(2,3)) {
    throw 'Original launcher does not have the expected history paths. Verified history was preserved; nothing overwritten.'
}
$NewLauncher = Join-Path $Repair 'Start-NFI-Backtest-Verified-History.ps1'
Set-Content -LiteralPath $NewLauncher -Value $Text.Replace($OldHistory, $History) -Encoding UTF8
Write-Host "UPLOAD_SUMMARY=$(Join-Path $History 'history-import-summary.json')"
Write-Host "MANUAL_BACKTEST=$NewLauncher"
Write-Host 'HISTORY_VERIFIED; ORIGINAL_STRATEGY_CONFIG_AND_IMPORT_PRESERVED; NO_BACKTEST_BOT_OR_GROK_STARTED'
