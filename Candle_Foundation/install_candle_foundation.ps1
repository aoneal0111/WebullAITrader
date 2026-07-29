$ErrorActionPreference = "Stop"

$RepoRoot = (Get-Location).Path
$InstallerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path (Join-Path $RepoRoot "app\market_data\models.py"))) {
    throw "Run this script from the WebullAITrader repository root."
}

$Status = git status --porcelain
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read Git status."
}
if ($Status) {
    throw "Working tree is not clean. Commit or stash current changes first."
}

Write-Host "Installing candle foundation..."
python (Join-Path $InstallerRoot "apply_candle_foundation.py")
if ($LASTEXITCODE -ne 0) { throw "Installer failed." }

Write-Host "`nRunning compile validation..."
python -m compileall app
if ($LASTEXITCODE -ne 0) { throw "compileall failed." }

Write-Host "`nRunning test suite..."
python -m pytest
if ($LASTEXITCODE -ne 0) { throw "pytest failed." }

Write-Host "`nChecking diff whitespace..."
git diff --check
if ($LASTEXITCODE -ne 0) { throw "git diff --check failed." }

Write-Host "`nInstallation and validation completed successfully."
Write-Host "Review with: git status --short"
Write-Host "Review with: git diff -- app/market_data tests/market_data"
