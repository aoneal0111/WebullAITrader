$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "============================================================"
Write-Host " WebullAITrader Professional Installer Builder"
Write-Host "============================================================"

if (-not (Test-Path ".\app\gui\app.py")) {
    throw "Missing app\gui\app.py. Run this script from the WebullAITrader project folder."
}

if (-not (Test-Path ".\WebullAITrader.png")) {
    throw "Save the icon as WebullAITrader.png in the project folder first."
}

$Python = (Get-Command python -ErrorAction Stop).Source
Write-Host "Using Python: $Python"

Write-Host "`nInstalling packaging tools..."
& $Python -m pip install --upgrade pyinstaller pillow

if ($LASTEXITCODE -ne 0) {
    throw "Unable to install PyInstaller or Pillow."
}

Write-Host "`nCreating Windows icon..."

@'
from pathlib import Path
from PIL import Image

source = Path("WebullAITrader.png")
output = Path("WebullAITrader.ico")

image = Image.open(source).convert("RGBA")
image.save(
    output,
    format="ICO",
    sizes=[
        (16, 16),
        (24, 24),
        (32, 32),
        (48, 48),
        (64, 64),
        (128, 128),
        (256, 256),
    ],
)

print(f"Created {output.resolve()}")
'@ | Set-Content ".\create_icon.py" -Encoding UTF8

& $Python ".\create_icon.py"

if ($LASTEXITCODE -ne 0) {
    throw "Icon conversion failed."
}

Write-Host "`nCreating application launcher..."

@'
"""PyInstaller launcher for WebullAITrader."""

from app.gui.app import main


if __name__ == "__main__":
    raise SystemExit(main())
'@ | Set-Content ".\webull_ai_trader_launcher.py" -Encoding UTF8

Write-Host "`nRemoving previous package builds..."

Remove-Item ".\build" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item ".\dist" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item ".\WebullAITrader.spec" -Force -ErrorAction SilentlyContinue
Remove-Item ".\installer_output" -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "`nBuilding WebullAITrader.exe..."

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "WebullAITrader" `
    --icon ".\WebullAITrader.ico" `
    --paths "." `
    --collect-all "PySide6" `
    ".\webull_ai_trader_launcher.py"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

$ApplicationExe = Join-Path `
    $ProjectRoot `
    "dist\WebullAITrader\WebullAITrader.exe"

if (-not (Test-Path $ApplicationExe)) {
    throw "WebullAITrader.exe was not created."
}

Write-Host "`nCreating Inno Setup configuration..."

@'
#define MyAppName "WebullAITrader"
#define MyAppVersion "4.0.0"
#define MyAppPublisher "Aric O'Neal"
#define MyAppExeName "WebullAITrader.exe"

[Setup]
AppId={{A73D4450-70AF-4F4A-8CF0-86FAD17BD23D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=WebullAITrader_Setup
SetupIconFile=WebullAITrader.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; \
    Description: "Create a desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"; \
    Flags: checkedonce

[Files]
Source: "dist\WebullAITrader\*"; \
    DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; \
    Filename: "{app}\{#MyAppExeName}"; \
    WorkingDir: "{app}"

Name: "{autodesktop}\{#MyAppName}"; \
    Filename: "{app}\{#MyAppExeName}"; \
    WorkingDir: "{app}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "Launch {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent
'@ | Set-Content ".\WebullAITrader.iss" -Encoding UTF8

$InnoSetupLocations = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)

$InnoCompiler = $InnoSetupLocations |
    Where-Object { Test-Path $_ } |
    Select-Object -First 1

if (-not $InnoCompiler) {
    Write-Host ""
    Write-Host "The standalone application was built successfully:"
    Write-Host $ApplicationExe
    Write-Host ""
    throw "Inno Setup 6 was not found. Install it, then run this script again."
}

Write-Host "`nBuilding professional Windows installer..."

& $InnoCompiler ".\WebullAITrader.iss"

if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed to build the installer."
}

$Installer = Join-Path `
    $ProjectRoot `
    "installer_output\WebullAITrader_Setup.exe"

if (-not (Test-Path $Installer)) {
    throw "The installer was not created."
}

Write-Host ""
Write-Host "============================================================"
Write-Host " INSTALLER BUILD COMPLETE"
Write-Host "============================================================"
Write-Host ""
Write-Host "Installer:"
Write-Host $Installer
Write-Host ""
Write-Host "Double-click WebullAITrader_Setup.exe to install the app."
Write-Host ""

Start-Process explorer.exe "/select,`"$Installer`""