param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$ResearchDirectory,
    [Parameter(Mandatory=$true)][string]$History
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$ResearchDirectory = (Resolve-Path -LiteralPath $ResearchDirectory).Path
$History = (Resolve-Path -LiteralPath $History).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python missing.' }
$ImportSummary = Get-Content -LiteralPath (Join-Path $History 'history-import-summary.json') -Raw | ConvertFrom-Json
if ($ImportSummary.status -ne 'VERIFIED_PINNED_CANDLE_FILES' -or $ImportSummary.expected_files -ne 800) {
    throw 'Verified pinned 800-file history import required; nothing downloaded or changed.'
}
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is outside reviewed branch.' }
$Repair = Join-Path $ResearchDirectory ('runtime-repair-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Repair
$Source = Join-Path $Repair 'review-source'
$Work = Join-Path $Repair 'backtest'
$Checks = Join-Path $env:TEMP ('atlas-nfi-checks-' + [guid]::NewGuid().ToString('N').Substring(0,12))
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Runtime repair checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/strategy_lab -q --basetemp $Checks
    if ($LASTEXITCODE -ne 0) { throw 'Runtime repair checks failed.' }
    & $Python -m app.strategy_lab.nfi_source_reproduction --source (Join-Path $ResearchDirectory 'nfi-source') --previous-work (Join-Path $ResearchDirectory 'backtest') --work $Work
    if ($LASTEXITCODE -ne 0) { throw 'Pinned source or trading configuration mismatch. Original files preserved.' }
}
finally { Pop-Location }
$Quote = { param($Value) "'" + $Value.Replace("'", "''") + "'" }
$Start = @'
$ErrorActionPreference = 'Stop'
Push-Location __SOURCE__
try {
    $Image = 'freqtradeorg/freqtrade:2026.9@sha256:989aff5636d8f0213ed44fe334c3314a194a51c0c755d16866960247aafd2eae'
    & docker run --rm --network none --read-only --tmpfs /tmp --entrypoint python --mount __CHECKMOUNT__ --mount __WORKMOUNT__ --mount __DATAMOUNT__ $Image /check/nfi_runtime_preflight.py
    if ($LASTEXITCODE -ne 0) { throw 'Offline runtime configuration preflight failed. See runtime-preflight.json; no backtest started.' }
    & __PYTHON__ -m app.strategy_lab.nfi_source_reproduction --work __WORK__ --history __HISTORY__
    if ($LASTEXITCODE -ne 0) { throw 'History/source verification failed.' }
    & docker run --rm --read-only --tmpfs /tmp --entrypoint python --mount __CHECKMOUNT__ --mount __WORKMOUNT__ --mount __DATAMOUNT__ $Image /check/nfi_native_backtest.py
    if ($LASTEXITCODE -ne 0) { throw 'Native backtest did not produce a successful result. Keep native-backtest.log and native-run-status.json. No summary was attempted.' }
    & __PYTHON__ -m app.strategy_lab.nfi_source_reproduction --work __WORK__ --summarize
    if ($LASTEXITCODE -ne 0) { throw 'Native trade/result reconciliation failed.' }
    Write-Host __SUMMARY__
}
finally { Pop-Location }
'@
$Text = $Start.Replace('__SOURCE__', (& $Quote $Source)).Replace('__PYTHON__', (& $Quote $Python)).Replace('__WORK__', (& $Quote $Work)).Replace('__HISTORY__', (& $Quote $History)).Replace('__CHECKMOUNT__', (& $Quote ("type=bind,source=" + (Join-Path $Source 'app\strategy_lab') + ',target=/check,readonly'))).Replace('__WORKMOUNT__', (& $Quote "type=bind,source=$Work,target=/work")).Replace('__DATAMOUNT__', (& $Quote "type=bind,source=$History,target=/history,readonly")).Replace('__SUMMARY__', (& $Quote ("UPLOAD_SUMMARY=" + (Join-Path $Work 'reproduction-summary.json'))))
$Launcher = Join-Path $Repair 'Start-NFI-Backtest-Checked.ps1'
Set-Content -LiteralPath $Launcher -Value $Text -Encoding UTF8
Write-Host "REPAIR_DIRECTORY=$Repair"
Write-Host "REUSED_HISTORY=$History"
Write-Host "MANUAL_BACKTEST=$Launcher"
Write-Host 'PREPARED_ONLY; NO_DATA_DOWNLOAD; ORIGINAL_SOURCE_CONFIG_AND_LOGS_PRESERVED; NO_BOT_OR_GROK_STARTED'
