# Read-only capture: never starts Atlas, Docker, a provider request or an order.
$ErrorActionPreference = 'Stop'
$Base = Join-Path $env:LOCALAPPDATA 'AtlasResearch'
$Root = Join-Path $Base ('runtime-recovery-review-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Files = @(Get-ChildItem (Join-Path $env:TEMP 'atlas-performance') -Filter '*.json' -File |
    Sort-Object LastWriteTime -Descending | Select-Object -First 3)
if (-not $Files.Count) { throw 'No performance diagnostics found. Nothing started.' }
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
