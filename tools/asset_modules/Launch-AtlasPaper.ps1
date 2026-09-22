param([string]$Repo = "C:\Users\aonea\WebullAITrader")
$ErrorActionPreference = "Stop"
$Manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot "manifest.json") -Raw | ConvertFrom-Json
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (!(Test-Path -LiteralPath $Python)) { throw "Atlas virtual environment missing." }
$Current = git -C $Repo rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $Current -ne $Manifest.commit) { throw "Install and validate this package first." }
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app'
}
if ($Running) { throw "Atlas is already running." }
$env:WEBULL_TRADING_ENVIRONMENT = "PAPER"
$env:LIVE_TRADING_ENABLED = "false"
$env:WARRIOR_FORWARD_PAPER_ENABLED = "true"
$env:ATLAS_WARRIOR_ADAPTIVE_CONTEXT_ENABLED = "true"
$env:TRADE_INTELLIGENCE_ENABLED = "true"
Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
$LogRoot = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "AtlasLogs"
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$OutputLog = Join-Path $LogRoot "atlas-$Stamp.stdout.log"
$ErrorLog = Join-Path $LogRoot "atlas-$Stamp.stderr.log"
$AtlasProcess = Start-Process -FilePath $Python -ArgumentList @("-m", "app.gui.app") `
    -WorkingDirectory $Repo -RedirectStandardOutput $OutputLog -RedirectStandardError $ErrorLog -PassThru
Write-Host "Atlas GUI launched in PAPER mode. PID: $($AtlasProcess.Id)"
Write-Host "Select Equity or Crypto, then Start market. AI paper proposals are OFF until enabled."
Write-Host "Output: $OutputLog"
Write-Host "Errors: $ErrorLog"
