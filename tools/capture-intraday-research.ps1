param(
    [string]$Repo = (Join-Path $env:USERPROFILE "WebullAITrader"),
    [string]$OutputDirectory,
    [string]$EquityCapture,
    [string]$CryptoCapture,
    [int]$Days = 45
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Atlas virtual-environment Python is missing." }
if (-not $EquityCapture) { $EquityCapture = Join-Path $Repo "data\warrior_momentum_v1_forward\forward_capture.sqlite3" }
if (-not $CryptoCapture) { $CryptoCapture = Join-Path $Repo "crypto-completed-bars.sqlite3" }
if (-not $OutputDirectory) {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    if (-not $Desktop) { $Desktop = $env:USERPROFILE }
    $OutputDirectory = Join-Path $Desktop ("atlas-intraday-research-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0,8))
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $OutputDirectory) { throw "Use a new output path." }
$SourceRoot = Join-Path $env:TEMP ("atlas-intraday-source-" + [guid]::NewGuid().ToString("N"))
$Worktree = Join-Path $SourceRoot "review-source"
$null = New-Item -ItemType Directory -Path $SourceRoot
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw "Research fetch failed." }
$Revision = (& git -C $Repo rev-parse FETCH_HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Revision lookup failed." }
& git -C $Repo worktree add --detach $Worktree $Revision
if ($LASTEXITCODE -ne 0) { throw "Review checkout creation failed." }
try {
    Push-Location $Worktree
    try {
        & $Python -m unittest tests.strategy_research.test_intraday_export -q
        if ($LASTEXITCODE -ne 0) { throw "Read-only export verification failed." }
        & $Python -m app.strategy_research.intraday_export --output $OutputDirectory --equity $EquityCapture --crypto $CryptoCapture --days $Days
        if ($LASTEXITCODE -ne 0) { throw "Saved-data export failed." }
        $ZipPath = [System.IO.Path]::ChangeExtension($OutputDirectory, ".zip")
        Write-Host "INTRADAY_ZIP=$ZipPath"
        & explorer.exe "/select,`"$ZipPath`""
    }
    finally { Pop-Location }
}
finally {
    & git -C $Repo worktree remove --force $Worktree
    if ($LASTEXITCODE -ne 0) { Write-Warning "Temporary source cleanup failed: $Worktree" }
}
