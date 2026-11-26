param(
    [Parameter(Mandatory=$true)][string]$Trial
)
$ErrorActionPreference = 'Stop'
$Trial = (Resolve-Path -LiteralPath $Trial).Path
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and
    $_.CommandLine -match 'app\.gui\.app|bootstrap-observer\.py|grok_day_proposals'
}
if ($Running) { throw 'Close Atlas and stop the Grok intentions process normally first. Nothing changed.' }
if (-not (Test-Path -LiteralPath (Join-Path $Trial 'experiment.json'))) {
    throw 'Existing trial specification required. No trial or budget was created.'
}
$Names = @('Start-Atlas-Paper-Trial.ps1', 'Start-Grok-Intentions.ps1')
$Prepared = @{}
foreach ($Name in $Names) {
    $Path = Join-Path $Trial $Name
    $Text = Get-Content -LiteralPath $Path -Raw
    if ($Text -match 'PAPER_SYMBOL_AUTHORIZATION_MODE') {
        throw 'Authorization setting already exists. Review it before replacing anything.'
    }
    $Needle = '$TrialEnvironment = @{'
    if ([regex]::Matches($Text, [regex]::Escape($Needle)).Count -ne 1) {
        throw "Unexpected launcher structure: $Name. Nothing changed."
    }
    $Line = "    'PAPER_SYMBOL_AUTHORIZATION_MODE' = 'DYNAMIC_WARRIOR_AND_QUICK_SCALPER'"
    $Prepared[$Name] = $Text.Replace($Needle, $Needle + "`r`n" + $Line)
}
$Backup = Join-Path $Trial ('before-symbol-authorization-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Backup
foreach ($Name in $Names) {
    Copy-Item -LiteralPath (Join-Path $Trial $Name) -Destination (Join-Path $Backup $Name)
}
foreach ($Name in $Names) {
    Set-Content -LiteralPath (Join-Path $Trial $Name) -Value $Prepared[$Name] -Encoding UTF8
}
$Results = Join-Path $Trial 'grok-order-intentions'
if (Test-Path -LiteralPath $Results) {
    $Archive = 'grok-order-intentions-archive-' + [guid]::NewGuid().ToString('N')
    Rename-Item -LiteralPath $Results -NewName $Archive
    Write-Host "PREVIOUS_GROK_RESULTS=$(Join-Path $Trial $Archive)"
}
Write-Host "LAUNCHER_BACKUP=$Backup"
Write-Host 'PAPER_SYMBOL_AUTHORIZATION=DYNAMIC_WARRIOR_AND_QUICK_SCALPER'
Write-Host 'PREPARED_ONLY; NO_ATLAS_OR_GROK_STARTED; NO_API_REQUESTS; NO_ENV_FILE_OR_REPOSITORY_CHANGED'
Write-Host 'EXISTING_TRIAL_ID_EXPIRY_AND_ENTRY_BUDGET_UNCHANGED; ORIGINAL_DOLLAR_AND_RISK_CAPS_RETAINED'
Write-Host "MANUAL_ATLAS=$(Join-Path $Trial 'Start-Atlas-Paper-Trial.ps1')"
Write-Host "MANUAL_GROK=$(Join-Path $Trial 'Start-Grok-Intentions.ps1')"
Write-Host 'Use the original launchers, not older diagnostic copies. Grok request cap applies per invocation.'
