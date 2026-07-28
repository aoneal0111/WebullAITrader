$ErrorActionPreference = "Stop"

$Repo = (Get-Location).Path
if (-not (Test-Path (Join-Path $Repo "app\gui"))) {
    throw "Run this script from the WebullAITrader repository root."
}

$PatchRoot = Join-Path $PSScriptRoot "app\gui"
$BackupRoot = Join-Path $Repo ("Atlas_GUI_Backup_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null

$Files = @(
    "app.py",
    "main_window.py",
    "models\dashboard.py",
    "models\__init__.py",
    "projections\dashboard_projection.py",
    "pages\dashboard.py",
    "pages\orders.py",
    "widgets\portfolio_metrics.py",
    "shell\sidebar.py"
)

foreach ($Relative in $Files) {
    $Source = Join-Path $PatchRoot $Relative
    $Destination = Join-Path (Join-Path $Repo "app\gui") $Relative
    $Backup = Join-Path $BackupRoot $Relative

    if (Test-Path $Destination) {
        New-Item -ItemType Directory -Path (Split-Path $Backup) -Force | Out-Null
        Copy-Item $Destination $Backup -Force
    }

    New-Item -ItemType Directory -Path (Split-Path $Destination) -Force | Out-Null
    Copy-Item $Source $Destination -Force
}

$ManualFiles = @(
    (Join-Path $Repo "app\gui\pages\order_entry.py"),
    (Join-Path $Repo "app\gui\widgets\order_entry_panel.py")
)
foreach ($ManualFile in $ManualFiles) {
    if (Test-Path $ManualFile) {
        $Relative = $ManualFile.Substring((Join-Path $Repo "app\gui").Length).TrimStart("\")
        $Backup = Join-Path $BackupRoot $Relative
        New-Item -ItemType Directory -Path (Split-Path $Backup) -Force | Out-Null
        Copy-Item $ManualFile $Backup -Force
        Remove-Item $ManualFile -Force
    }
}

Write-Host "Patch applied." -ForegroundColor Green
Write-Host "Backup: $BackupRoot" -ForegroundColor Cyan
Write-Host "Run: python -m pytest" -ForegroundColor Yellow
Write-Host "Then: python -m app.gui.app" -ForegroundColor Yellow
