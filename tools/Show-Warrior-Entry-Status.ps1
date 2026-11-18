param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$PaperDirectory
)
# Read local recording/controller diagnostics; never start Atlas or call a provider.
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$PaperDirectory = (Resolve-Path -LiteralPath $PaperDirectory).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Out = Join-Path $env:LOCALAPPDATA ('AtlasResearch\warrior-entry-status-' + [guid]::NewGuid().ToString('N') + '.json')
Push-Location $Repo
try {
    $Lines = & $Python -m app.asset_modules.paper_morning_status --trial $PaperDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Local status read failed. Trading unchanged.' }
    $Lines | Set-Content -LiteralPath $Out -Encoding UTF8
} finally { Pop-Location }
Write-Host "UPLOAD_FILE=$Out"
explorer.exe "/select,`"$Out`""
