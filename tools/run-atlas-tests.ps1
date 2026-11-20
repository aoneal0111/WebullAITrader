#Requires -Version 5.1
<#
.SYNOPSIS
  Run a predictable Atlas test tier without changing trading state.
.DESCRIPTION
  Fast: gateway/order-book tests for ordinary development.
  Critical: position protection, Warrior execution and gateway tests.
  Full: the complete pytest collection (no deletion or hidden exclusion).
  All tiers are local pytest processes. No Atlas runtime is started.
.EXAMPLE
  .\tools\run-atlas-tests.ps1 -Tier Fast
.EXAMPLE
  .\tools\run-atlas-tests.ps1 -Tier Critical
.EXAMPLE
  .\tools\run-atlas-tests.ps1 -Tier Full
#>
[CmdletBinding()]
param(
    [ValidateSet("Fast", "Critical", "Full")]
    [string]$Tier = "Fast",
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $Root ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python interpreter not found. Use -PythonPath to select an installed virtual environment."
}

$Fast = @(
    "tests/paper_gateway/test_gateway.py",
    "tests/paper_order_book/test_cancel_integration.py",
    "tests/paper_order_book/test_policy.py"
)
$Critical = @(
    "tests/paper_gateway",
    "tests/paper_order_book",
    "tests/warrior_momentum",
    "tests/strategies/test_autonomous_paper_bridge.py",
    "tests/test_paper_position_protection_integrity.py",
    "tests/integration/test_paper_restart_exit_projection.py"
)

$Targets = switch ($Tier) {
    "Fast" { $Fast }
    "Critical" { $Critical }
    "Full" { @("tests") }
}

foreach ($Target in $Targets) {
    if (-not (Test-Path -LiteralPath (Join-Path $Root $Target))) {
        throw "Test target missing: $Target"
    }
}

Push-Location $Root
try {
    Write-Host "ATLAS_TEST_TIER=$Tier"
    Write-Host "ATLAS_TEST_ROOT=$Root"
    & $PythonPath -m pytest -q @Targets
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
Write-Host "ATLAS_TEST_EXIT=$ExitCode"
exit $ExitCode
