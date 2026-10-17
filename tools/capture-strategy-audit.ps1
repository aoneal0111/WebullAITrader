param(
    [string]$Repo = (Join-Path $env:USERPROFILE "WebullAITrader"),
    [string]$Ledger,
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Atlas virtual-environment Python is missing." }
if (-not $Ledger) { $Ledger = Join-Path $Repo "data\sandbox\paper-execution.sqlite3" }
if (-not (Test-Path -LiteralPath $Ledger -PathType Leaf)) { throw "Execution ledger not found: $Ledger" }
$Ledger = (Resolve-Path -LiteralPath $Ledger).Path
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $env:TEMP ("atlas-strategy-audit-" + [guid]::NewGuid().ToString("N"))
}
$null = New-Item -ItemType Directory -Path $OutputDirectory -Force
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path
$Worktree = Join-Path $OutputDirectory "review-source"
$TestRoot = Join-Path $OutputDirectory "pytest"

# Fetch and test review code in a separate checkout. The operator's current
# branch and runtime checkout are not switched or modified.
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw "Research branch fetch failed." }
$Revision = (& git -C $Repo rev-parse FETCH_HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Research revision lookup failed." }
& git -C $Repo worktree add --detach $Worktree $Revision
if ($LASTEXITCODE -ne 0) { throw "Review checkout creation failed." }

Push-Location $Worktree
try {
    & $Python -m pytest tests/strategy_research tests/asset_modules/test_learning_outcomes.py tests/test_indicators.py -q --basetemp $TestRoot
    if ($LASTEXITCODE -ne 0) { throw "Strategy audit verification failed." }
    $AuditLines = & $Python -m app.strategy_research.audit --ledger $Ledger
    if ($LASTEXITCODE -ne 0) { throw "Read-only equity audit failed." }
    $AuditJson = $AuditLines -join [Environment]::NewLine
    $Audit = $AuditJson | ConvertFrom-Json
    if ($Audit.execution_authorized -ne $false -or $Audit.orders -ne 0) {
        throw "Unexpected audit scope."
    }
    $Utf8 = [System.Text.UTF8Encoding]::new($false)
    $AuditPath = Join-Path $OutputDirectory "atlas-equity-strategy-audit.json"
    [System.IO.File]::WriteAllText($AuditPath, $AuditJson, $Utf8)
    $ManifestPath = Join-Path $OutputDirectory "source-revision.txt"
    [System.IO.File]::WriteAllText($ManifestPath, $Revision, $Utf8)
    $ZipPath = Join-Path $OutputDirectory "atlas-equity-strategy-audit.zip"
    Compress-Archive -LiteralPath @($AuditPath, $ManifestPath) -DestinationPath $ZipPath
    $Audit.summary.groups | Format-Table engine, strategy_family, closed_lifecycles, wins, losses, win_rate, net_pnl
    Write-Host "AUDIT_ZIP=$ZipPath"
    Write-Host "Campaign: $($Audit.source.campaign); exclusions are included in the JSON."
}
finally {
    Pop-Location
}
