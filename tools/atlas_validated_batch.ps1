# Atlas consolidated PAPER-only research update. Does not start Atlas or send orders.
$ErrorActionPreference = 'Stop'
$Repo = Join-Path $HOME 'WebullAITrader'
$Py = Join-Path $Repo '.venv\Scripts\python.exe'
$Expected = '13be31a5b16e8998fa4f23f73392b437b0dda5f0'
$Branch = 'research/high-win-rate-comparison'
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\validated-batch-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Root -Force | Out-Null
$Log = Join-Path $Root 'validation.log'

if (-not (Test-Path -LiteralPath $Py -PathType Leaf)) { throw 'ATLAS_VENV_NOT_FOUND' }
$Running = @(Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -match '^(python|pythonw)\.exe$' -and $_.CommandLine -match 'app\.gui\.app|webull_ai_trader_launcher') -or
    ($_.Name -match '^WebullAITrader\.exe$')
})
if ($Running.Count -gt 0) { throw 'STOP_ATLAS_BEFORE_INSTALLING' }

Push-Location $Repo
try {
    if ((git branch --show-current) -ne $Branch) { throw 'UNEXPECTED_GIT_BRANCH' }
    $TrackedChanges = @(git status --porcelain --untracked-files=no)
    if ($TrackedChanges.Count -ne 0) { throw 'TRACKED_WORKTREE_CHANGES_PRESENT' }

    git fetch origin $Branch *> $Log
    if ($LASTEXITCODE -ne 0) { throw 'FETCH_FAILED' }
    $Remote = git rev-parse "origin/$Branch"
    if ($LASTEXITCODE -ne 0 -or $Remote -ne $Expected) { throw 'REMOTE_HEAD_NOT_EXPECTED' }

    git merge --ff-only "origin/$Branch" *>> $Log
    if ($LASTEXITCODE -ne 0) { throw 'FAST_FORWARD_INSTALL_FAILED' }

    # Subprocess environment only. No edits to local .env or trading policy.
    $env:TRADING_ENVIRONMENT = 'PAPER'
    $env:WEBULL_TRADING_ENVIRONMENT = 'PAPER'
    $env:LIVE_TRADING_ENABLED = 'false'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
    $TempTests = Join-Path $Root 'pytest-temp'
    $Tests = @(
        'tests/strategy_research/test_warrior_paper_entry_intervals.py',
        'tests/strategy_research/test_atlas_startup_entry_health.py',
        'tests/strategy_research/test_warrior_micro_outcome_screen.py',
        'tests/gui/test_launcher_startup_phase_timing.py',
        'tests/composition/test_desktop_startup_phase_timing.py'
    )
    & $Py -m pytest -q -p no:cacheprovider "--basetemp=$TempTests" @Tests *>> $Log
    if ($LASTEXITCODE -ne 0) { throw "FOCUSED_TESTS_FAILED; LOG=$Log" }

    $Reports = Join-Path $env:LOCALAPPDATA 'AtlasResearch\warrior-full-history-988fa42fe15c4a1d9329bbefaf18a5d0'
    $Normalized = Join-Path $env:LOCALAPPDATA 'AtlasResearch\selected-sip-history-fadd7a7efc22485cb06c8c29575d1bb7\history\normalized'
    $Outcomes = Join-Path $Root 'historical-micro-outcomes.json'
    if ((Test-Path -LiteralPath $Reports -PathType Container) -and
        (Test-Path -LiteralPath $Normalized -PathType Container)) {
        $Positive = Join-Path $Root 'positive-trigger-reports'
        New-Item -ItemType Directory -Path $Positive | Out-Null
        $Selected = 0
        Get-ChildItem -LiteralPath $Reports -Filter '*-timing.json' -File | ForEach-Object {
            $Report = Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json
            if (@($Report.observations).Count -gt 0) {
                Copy-Item -LiteralPath $_.FullName -Destination $Positive
                $Selected++
            }
        }
        if ($Selected -eq 0) { throw 'NO_POSITIVE_TRIGGER_REPORTS' }
        # $0.01 per share per side is an assumption, NOT an observed cost.
        & $Py -m app.strategy_research.warrior_micro_outcome_screen `
            --reports "$Positive" --normalized "$Normalized" `
            --output "$Outcomes" --horizon-bars 30 `
            --cost-per-side-per-share 0.01 *>> $Log
        if ($LASTEXITCODE -ne 0) { throw "OUTCOME_SCREEN_FAILED; LOG=$Log" }
        Write-Host "POSITIVE_REPORTS=$Selected"
        Write-Host "OUTCOME_REPORT=$Outcomes"
    } else {
        Write-Host 'OUTCOME_REPORT=NOT_RUN_HISTORICAL_DATA_MISSING'
    }
    Write-Host 'FOCUSED_TESTS=PASSED'
    Write-Host "ATLAS_HEAD=$(git rev-parse HEAD)"
    Write-Host "LOG=$Log"
    Write-Host 'PAPER_ONLY_BATCH_INSTALLED'
} finally {
    Pop-Location
}
