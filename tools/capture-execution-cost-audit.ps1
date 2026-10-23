param(
    [string]$Repo = (Join-Path $env:USERPROFILE "WebullAITrader"),
    [string]$Ledger,
    [string]$OutputDirectory,
    [ValidateRange(1,60000)][int]$MaxQuoteAgeMs = 2000,
    [ValidateRange(1,60000)][int]$MaxFillDelayMs = 5000
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Atlas virtual-environment Python is missing." }
if (-not $Ledger) { $Ledger = Join-Path $Repo "data\sandbox\paper-execution.sqlite3" }
$Ledger = (Resolve-Path -LiteralPath $Ledger).Path
if (-not $OutputDirectory) {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    if (-not $Desktop) { $Desktop = $env:USERPROFILE }
    $OutputDirectory = Join-Path $Desktop ("atlas-execution-cost-audit-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0,8))
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$ZipPath = $OutputDirectory + ".zip"
if ((Test-Path -LiteralPath $OutputDirectory) -or (Test-Path -LiteralPath $ZipPath)) { throw "Use a new output path." }
$Worktree = Join-Path $env:TEMP ("atlas-cost-source-" + [guid]::NewGuid().ToString("N"))
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw "Research branch fetch failed." }
$Revision = (& git -C $Repo rev-parse FETCH_HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Research revision lookup failed." }
& git -C $Repo worktree add --detach $Worktree $Revision
if ($LASTEXITCODE -ne 0) { throw "Review checkout creation failed." }
try {
    Push-Location $Worktree
    try {
        # The cost audit uses only stdlib imports, including under Windows policy.
        & $Python -S -m unittest tests.strategy_research.test_execution_cost_audit -q
        if ($LASTEXITCODE -ne 0) { throw "Execution cost audit verification failed." }
        $Lines = & $Python -S -m app.strategy_research.execution_cost_audit --ledger $Ledger --max-quote-age-ms $MaxQuoteAgeMs --max-fill-delay-ms $MaxFillDelayMs
        if ($LASTEXITCODE -ne 0) { throw "Read-only execution cost audit failed. No report ZIP was produced." }
        $Json = $Lines -join [Environment]::NewLine
        $Report = $Json | ConvertFrom-Json
        if ($Report.execution_authorized -ne $false -or $Report.costs_verified -ne $false -or $Report.orders -ne 0) {
            throw "Unexpected audit scope."
        }
        $null = New-Item -ItemType Directory -Path $OutputDirectory
        $Utf8 = [System.Text.UTF8Encoding]::new($false)
        $ReportPath = Join-Path $OutputDirectory "execution-cost-audit.json"
        $RevisionPath = Join-Path $OutputDirectory "source-revision.txt"
        [System.IO.File]::WriteAllText($ReportPath,$Json,$Utf8)
        [System.IO.File]::WriteAllText($RevisionPath,$Revision,$Utf8)
        Compress-Archive -LiteralPath @($ReportPath,$RevisionPath) -DestinationPath $ZipPath
        Write-Host "FILLS=$($Report.fills) DECISION_REFERENCE_MATCHES=$($Report.decision_reference_matches) UNMATCHED=$($Report.unmatched_fills)"
        Write-Host "STATUS=$($Report.status)"
        Write-Host "COST_AUDIT_ZIP=$ZipPath"
        & explorer.exe "/select,`"$ZipPath`""
    }
    finally { Pop-Location }
}
finally {
    & git -C $Repo worktree remove --force $Worktree
    if ($LASTEXITCODE -ne 0) { Write-Warning "Temporary source cleanup failed: $Worktree" }
}
