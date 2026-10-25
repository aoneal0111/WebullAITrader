param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory = $true)][string]$Revision
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[a-f0-9]{40}$') { throw 'An exact reviewed commit is required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python is missing.' }
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and
    $_.CommandLine -match 'app\.gui\.app|bootstrap-observer\.py'
}
if ($Running) { throw 'Close Atlas normally before updating. Nothing was changed.' }
$Dirty = & git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0) { throw 'Repository check failed.' }
if ($Dirty) { throw 'Tracked local changes exist. Nothing was overwritten.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Reviewed revision is not on the fetched research history.' }
& git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Your branch needs integration. Nothing was changed.' }
$Backup = 'backup/before-profit-ack-fix-' + [guid]::NewGuid().ToString('N')
& git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup creation failed.' }
Write-Host "BACKUP_BRANCH=$Backup"
& git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset or overwrite was attempted.' }
$Installed = (& git -C $Repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $Installed -ne $Revision) { throw 'Installed revision check failed.' }
Push-Location $Repo
try {
    $TestTemp = Join-Path $env:TEMP ('atlas-profit-ack-tests-' + [guid]::NewGuid().ToString('N'))
    & $Python -m pytest `
        tests/warrior_momentum/test_exit_request_acknowledgement.py `
        tests/warrior_momentum/test_point_setup_continuity.py `
        tests/warrior_momentum/test_durable_target_reconciliation.py `
        tests/strategies/test_autonomous_paper_bridge.py `
        tests/equity_overhaul/test_admission.py `
        --basetemp $TestTemp -q
    if ($LASTEXITCODE -ne 0) {
        throw "Windows checks failed. Keep Atlas closed; BACKUP_BRANCH=$Backup. No automatic reset was performed."
    }
    Write-Host "UPDATE_VERIFIED=$Installed"
    # The old capture receipt is intentionally not reused after source changes.
    $Output = Join-Path $env:LOCALAPPDATA ('AtlasResearch\atlas-fresh-equity-capture-' + [guid]::NewGuid().ToString('N'))
    & (Join-Path $Repo 'tools\prepare-observer-session.ps1') `
        -Repo $Repo -Revision $Revision -OutputDirectory $Output
    if ($LASTEXITCODE -ne 0) { throw 'Capture preparation did not pass. Atlas was not started.' }
    Write-Host 'REPAIRS_INSTALLED; FRESH_CAPTURE_PREPARED; ATLAS_NOT_STARTED'
}
finally { Pop-Location }
