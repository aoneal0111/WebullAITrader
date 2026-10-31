param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|bootstrap-observer\.py|grok_day_proposals'
}
if ($Running) { throw 'Close Atlas and old Grok intentions normally first. Nothing changed.' }
$Dirty = git -C $Repo status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw 'Tracked source changes found. Nothing overwritten.' }
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is not on the reviewed branch.' }
git -C $Repo merge-base --is-ancestor HEAD $Revision
if ($LASTEXITCODE -ne 0) { throw 'Repository requires integration. No reset performed.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\crypto-desktop-observation-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/asset_modules/test_crypto_desktop_capture.py tests/asset_modules/test_crypto_native_observer.py tests/asset_modules/test_native_crypto_desktop_composition.py tests/asset_modules/test_daily_research.py tests/asset_modules/test_daily_observer_protocol.py tests/asset_modules/test_crypto_worker.py tests/asset_modules/test_crypto_allocation.py tests/asset_modules/test_crypto_durable_intents.py tests/crypto_research/test_provider_worker.py tests/asset_modules/test_grok_execution_crypto.py -q --basetemp (Join-Path $Root 'checks')
    if ($LASTEXITCODE -ne 0) { throw 'Windows checks failed. Installed source and settings unchanged.' }
} finally { Pop-Location }
$Backup = 'backup/before-crypto-observation-' + [guid]::NewGuid().ToString('N')
git -C $Repo branch $Backup HEAD
if ($LASTEXITCODE -ne 0) { throw 'Backup failed.' }
git -C $Repo merge --ff-only $Revision
if ($LASTEXITCODE -ne 0) { throw 'Update failed. No reset performed.' }
Push-Location $Repo
try {
    & $Python -m app.asset_modules.crypto_desktop_capture --repo $Repo --output (Join-Path $Root 'capture')
    if ($LASTEXITCODE -ne 0) { throw 'Source installed; capture preparation failed. Nothing launched.' }
} finally { Pop-Location }
Write-Host "UPDATE_VERIFIED=$Revision"
Write-Host "BACKUP_BRANCH=$Backup"
Write-Host 'PREPARED_ONLY; ATLAS_NOT_STARTED; NO_API_REQUESTS; EQUITY_TRIALS_AND_ENV_FILE_UNCHANGED'
Write-Host 'Run MANUAL_ATLAS yourself. Select Crypto, activate its market module, and leave native observation entries disabled.'
