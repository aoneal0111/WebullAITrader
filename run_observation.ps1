$ErrorActionPreference = "Stop"

$env:BROKER_INTEGRATION_MODE = "SANDBOX"
$env:LIVE_TRADING_ENABLED = "false"

$env:WEBULL_API_KEY = Read-Host "Webull API key"
$env:WEBULL_API_SECRET = Read-Host "Webull API secret"
$env:WEBULL_ACCOUNT_ID = Read-Host "Webull account ID"

.\.venv\Scripts\python.exe -m app.operational_main `
    --run `
    --max-cycles 10 `
    --interval-seconds 30

Remove-Item Env:WEBULL_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:WEBULL_API_SECRET -ErrorAction SilentlyContinue
Remove-Item Env:WEBULL_ACCOUNT_ID -ErrorAction SilentlyContinue
