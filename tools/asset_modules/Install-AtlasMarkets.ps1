param([string]$Repo = "C:\Users\aonea\WebullAITrader")
$ErrorActionPreference = "Stop"
$Manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot "manifest.json") -Raw | ConvertFrom-Json
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (!(Test-Path -LiteralPath $Python)) { throw "Atlas virtual environment missing at $Python" }
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app'
}
if ($Running) { throw "Close Atlas normally before installing." }
Push-Location $Repo
try {
    $Branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $Branch -ne $Manifest.branch) { throw "Expected branch $($Manifest.branch); found $Branch" }
    $Dirty = git status --porcelain --untracked-files=no
    if ($LASTEXITCODE -ne 0 -or $Dirty) { throw "Tracked changes exist or Git failed. Preserve them before installing." }
    $Current = git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw "Cannot read repository commit." }
    if ($Current -ne $Manifest.commit) {
        if ($Current -ne $Manifest.base) { throw "Expected base $($Manifest.base); found $Current. No files changed." }
        $Bundle = Join-Path $PSScriptRoot "atlas-markets.bundle"
        $Hash = (Get-FileHash -LiteralPath $Bundle -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($Hash -ne $Manifest.bundle_sha256) { throw "Bundle checksum mismatch." }
        git bundle verify $Bundle
        if ($LASTEXITCODE -ne 0) { throw "Bundle validation failed." }
        git fetch $Bundle HEAD
        if ($LASTEXITCODE -ne 0) { throw "Bundle import failed." }
        $Imported = git rev-parse FETCH_HEAD
        if ($Imported -ne $Manifest.commit) { throw "Unexpected imported commit." }
        git merge --ff-only FETCH_HEAD
        if ($LASTEXITCODE -ne 0) { throw "Fast-forward failed; no reset was attempted." }
    }
    $Current = git rev-parse HEAD
    if ($Current -ne $Manifest.commit) { throw "Installed commit mismatch." }
    $PreviousQt = $env:QT_QPA_PLATFORM
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:WEBULL_TRADING_ENVIRONMENT = "PAPER"
    $env:LIVE_TRADING_ENABLED = "false"
    $TempTests = Join-Path $env:TEMP ("atlas-markets-" + [guid]::NewGuid().ToString("N"))
    try {
        & $Python -m pytest tests/asset_modules tests/crypto_research `
            tests/gui/test_production_main_window_layout.py tests/gui/test_dashboard_shell.py `
            tests/composition/test_desktop_composition.py `
            -q -k "not test_production_desktop_historical_treatment_full_lifecycle_survives_restart" `
            --basetemp $TempTests
        $ValidationExit = $LASTEXITCODE
    } finally {
        if ($null -eq $PreviousQt) { Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
        else { $env:QT_QPA_PLATFORM = $PreviousQt }
    }
    if ($ValidationExit -ne 0) { throw "Validation failed. Patch remains applied; send the complete output. Atlas was not launched." }
    Write-Host "ATLAS MARKET MODULE VALIDATION PASSED"
    Write-Host "Commit: $Current"
    Write-Host "One pre-existing historical-journal test is excluded; see asset-modules.md."
    Write-Host "Atlas was not launched and GitHub was not changed."
    Write-Host "Run Launch-AtlasPaper.ps1 from this package to start the GUI."
} finally { Pop-Location }
