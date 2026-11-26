param([string]$DiagnosticPath = '', [string]$RunId = '')
# Read-only capture: never starts Atlas, Docker, a provider request or an order.
$ErrorActionPreference = 'Stop'
$Base = Join-Path $env:LOCALAPPDATA 'AtlasResearch'
$Root = Join-Path $Base ('runtime-recovery-review-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
if ($RunId) {
    if ($DiagnosticPath) { throw 'Supply either RunId or DiagnosticPath, not both.' }
    if ($RunId -notmatch '^[0-9a-f]{32}$') { throw 'Exact Atlas performance run ID required.' }
    $DiagnosticPath = Join-Path $env:TEMP "atlas-performance\$RunId.json"
}
if (-not $DiagnosticPath) {
    throw 'Explicit DiagnosticPath or RunId required. Latest files may belong to installer tests.'
}
$File = Get-Item -LiteralPath $DiagnosticPath
if ($File.Length -gt 16MB) { throw 'Diagnostic exceeds compact bound.' }
$Diagnostic = Get-Content -LiteralPath $File.FullName -Raw | ConvertFrom-Json
if (-not $Diagnostic.run_id -or -not $Diagnostic.startup) { throw 'Atlas performance artifact required.' }
if ($RunId -and $Diagnostic.run_id -ne $RunId) { throw 'Artifact run ID mismatch.' }
$Files = @($File)
$Copied = @()
foreach ($File in $Files) {
    if ($File.Length -gt 16MB) { throw "Diagnostic exceeds compact bound: $($File.Name)" }
    # Preserve original bytes: capture includes component timings and safe stream
    # identity diagnostics omitted by the earlier abbreviated status command.
    Copy-Item -LiteralPath $File.FullName -Destination (Join-Path $Root $File.Name)
    $Copied += $File.Name
}
[pscustomobject]@{
    captured_at = (Get-Date -Format o)
    scope = 'READ_ONLY_RUNTIME_RECOVERY_REVIEW'
    copied_diagnostics = $Copied
    commands_starting_trading = 0
    provider_requests = 0
} | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $Root 'capture-info.json') -Encoding UTF8
$Zip = "$Root.zip"
Compress-Archive -Path (Join-Path $Root '*.json') -DestinationPath $Zip
Write-Host "UPLOAD_FILE=$Zip"
explorer.exe "/select,`"$Zip`""
