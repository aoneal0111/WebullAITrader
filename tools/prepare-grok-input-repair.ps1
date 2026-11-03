param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Trial,
    [switch]$StartObserver
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Trial = (Resolve-Path -LiteralPath $Trial).Path
$Experiment = Get-Content -LiteralPath (Join-Path $Trial 'experiment.json') -Raw | ConvertFrom-Json
if ([DateTimeOffset]::UtcNow -ge [DateTimeOffset]$Experiment.expires_at) { throw 'Existing trial expired; no deadline was renewed.' }
$OldLauncher = Join-Path $Trial 'Start-Grok-Teams.ps1'
if (-not (Test-Path -LiteralPath $OldLauncher -PathType Leaf)) { throw 'Existing team launcher missing.' }
$Grok = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.asset_modules\.grok_day_proposals'
}
if ($Grok) { throw 'Press Ctrl+C in the Grok shell and wait for its summary first. Leave Atlas running.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is outside reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\grok-input-repair-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Observer-only checkout failed. Installed source unchanged.' }
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
Push-Location $Source
try {
    & $Python -m pytest tests/asset_modules/test_grok_input_health.py tests/asset_modules/test_grok_engine_teams.py tests/asset_modules/test_grok_day_proposals.py tests/asset_modules/test_grok_news_scout.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Observer checks failed. Nothing started or installed.' }
} finally { Pop-Location }
$Text = Get-Content -LiteralPath $OldLauncher -Raw
$Quote = { param($Value) "'" + $Value.Replace("'", "''") + "'" }
$OldLocation = 'Push-Location ' + (& $Quote $Repo)
if ([regex]::Matches($Text,[regex]::Escape($OldLocation)).Count -ne 1 -or
    $Text -notmatch 'grok_day_proposals' -or $Text -notmatch '\-\-teams') {
    throw 'Exact original team launcher shape required. Nothing replaced.'
}
$Text = $Text.Replace($OldLocation,('Push-Location ' + (& $Quote $Source)))
$NewLauncher = Join-Path $Root 'Start-Grok-Teams-Repaired.ps1'
Set-Content -LiteralPath $NewLauncher -Value $Text -Encoding UTF8
$Status = Join-Path $Root 'Show-Grok-Input.ps1'
$StatusText = '$ErrorActionPreference = ''Stop''' + "`n" + 'Push-Location ' + (& $Quote $Source) + "`ntry {`n    & " +
    (& $Quote $Python) + ' -m app.asset_modules.grok_input_health --trial ' + (& $Quote $Trial) + "`n} finally { Pop-Location }`n"
Set-Content -LiteralPath $Status -Value $StatusText -Encoding UTF8
Write-Host "MANUAL_GROK_REPAIRED=$NewLauncher"
Write-Host "READ_ONLY_INPUT_HEALTH=$Status"
Write-Host 'PREPARED_ONLY; ATLAS_LEFT_RUNNING; INSTALLED_SOURCE_ENV_LAUNCHERS_AND_TRIAL_UNCHANGED'
Write-Host 'EXISTING_60_ATTEMPT_BUDGET_HOURLY_SLOTS_AND_EXPIRY_REUSED; NO_API_REQUESTS; NO_ORDERS'

if ($StartObserver) {
    Write-Host 'STARTING_REPAIRED_GROK_AT_OPERATOR_REQUEST; NATIVE_ATLAS_UNCHANGED'
    & $NewLauncher
}
