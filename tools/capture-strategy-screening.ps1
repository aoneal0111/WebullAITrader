param(
    [string]$Repo = (Join-Path $env:USERPROFILE "WebullAITrader"),
    [string]$OutputDirectory,
    [string]$SplitAt,
    [string]$AdverseBpsPerSide = "10",
    [string]$FeePerSharePerSide = "0.005"
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Atlas virtual-environment Python is missing." }
if (-not $OutputDirectory) {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    if (-not $Desktop) { $Desktop = $env:USERPROFILE }
    $OutputDirectory = Join-Path $Desktop ("atlas-strategy-screening-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $OutputDirectory) { throw "Use a new output directory." }
$SourceRoot = Join-Path $env:TEMP ("atlas-screening-source-" + [guid]::NewGuid().ToString("N"))
$Worktree = Join-Path $SourceRoot "review-source"
$null = New-Item -ItemType Directory -Path $SourceRoot

& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw "Research branch fetch failed." }
$Revision = (& git -C $Repo rev-parse FETCH_HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Research revision lookup failed." }
& git -C $Repo worktree add --detach $Worktree $Revision
if ($LASTEXITCODE -ne 0) { throw "Review checkout creation failed." }

try {
    Push-Location $Worktree
    try {
        & $Python -m unittest tests.strategy_research.test_capture tests.strategy_research.test_comparison -q
        if ($LASTEXITCODE -ne 0) {
            throw "Screening verification failed. Dependencies must load under your existing Windows policy; do not disable Application Control."
        }
    }
    finally { Pop-Location }

    # Keep .env resolution in the existing repo, but explicitly import reviewed
    # source first. No credentials are copied into the worktree or output ZIP.
    Push-Location $Repo
    try {
        $Arguments = @("--output", $OutputDirectory, "--adverse-bps-per-side", $AdverseBpsPerSide, "--fee-per-share-per-side", $FeePerSharePerSide)
        if ($SplitAt) { $Arguments += @("--split-at", $SplitAt) }
        & $Python -c "import sys; sys.path.insert(0, sys.argv.pop(1)); from app.strategy_research.capture import main; raise SystemExit(main())" $Worktree @Arguments
        $CaptureExit = $LASTEXITCODE
        $ZipPath = [System.IO.Path]::ChangeExtension($OutputDirectory, ".zip")
        if (Test-Path -LiteralPath $ZipPath -PathType Leaf) {
            Write-Host "SCREENING_ZIP=$ZipPath"
            & explorer.exe "/select,$ZipPath"
        }
        if ($CaptureExit -ne 0) {
            throw "Capture incomplete. If a ZIP was produced, its manifest lists sanitized per-symbol errors. No strategy was activated."
        }
    }
    finally { Pop-Location }
}
finally {
    # Only this script's disposable source checkout is removed. Data stays on
    # the Desktop and the running Atlas checkout is never switched or launched.
    & git -C $Repo worktree remove --force $Worktree
    if ($LASTEXITCODE -ne 0) { Write-Warning "Temporary source cleanup failed: $Worktree" }
}
