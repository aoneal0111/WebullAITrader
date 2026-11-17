param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [ValidateRange(1,30)][int]$Seconds = 10,
    [ValidatePattern('^[A-Za-z0-9]{1,10}$')][string]$Symbol = 'AAPL'
)
$ErrorActionPreference = 'Stop'
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app'
}
if ($Running) { throw 'Close Atlas before the separate read-only market-data check.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Out = Join-Path $env:LOCALAPPDATA ('AtlasResearch\stream-connect-review-' + [guid]::NewGuid().ToString('N') + '.json')
Write-Host 'READ_ONLY_MARKET_DATA_CONNECT_AND_ONE_SYMBOL_SUBSCRIPTION; NO_ATLAS_RUNTIME; NO_GROK; NO_ORDERS'
Push-Location $Repo
try {
    & $Python -m app.webull.stream_connect_review --repo $Repo --output $Out --seconds $Seconds --symbol $Symbol
    if ($LASTEXITCODE -ne 0) { throw 'Connection check could not produce its report. Preserve the error.' }
} finally { Pop-Location }
Write-Host "UPLOAD_FILE=$Out"
explorer.exe "/select,`"$Out`""
