param([string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'), [string]$Revision, [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python is missing.' }
$Running = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?\.exe$' -and
    $_.CommandLine -match 'app\.gui\.app|bootstrap-observer\.py'
}
if ($Running) { throw 'Close Atlas normally before preparing the source snapshot. Nothing was overwritten.' }
if (-not $Revision) {
    & git -C $Repo fetch origin research/high-win-rate-comparison
    if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
    $Revision = (& git -C $Repo rev-parse FETCH_HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Revision lookup failed.' }
}
$Root = Join-Path $env:TEMP ('atlas-observer-prep-' + [guid]::NewGuid().ToString('N'))
$Worktree = Join-Path $Root 'source'
$null = New-Item -ItemType Directory -Path $Root
& git -C $Repo worktree add --detach $Worktree $Revision
if ($LASTEXITCODE -ne 0) { throw 'Source checkout failed.' }
try {
    Push-Location $Worktree
    try {
        & $Python -m pytest tests/asset_modules/test_grok_observer_session.py --basetemp (Join-Path $Root 'tests') -q
        if ($LASTEXITCODE -ne 0) { throw 'Observer preparation tests failed.' }
        if ($OutputDirectory) {
            $Output = [IO.Path]::GetFullPath($OutputDirectory)
        }
        else {
            $Desktop = [Environment]::GetFolderPath('Desktop')
            if (-not $Desktop) { $Desktop = $env:USERPROFILE }
            $Output = Join-Path $Desktop ('atlas-fresh-equity-capture-' + [guid]::NewGuid().ToString('N'))
        }
        & $Python -m app.asset_modules.grok_observer_session --repo $Repo --output $Output
        $PrepareExit = $LASTEXITCODE
        $Summary = Join-Path $Output 'preparation-summary.json'
        if (Test-Path -LiteralPath $Summary -PathType Leaf) { & explorer.exe "/select,`"$Summary`"" }
        if ($PrepareExit -ne 0) { throw 'Observer source preparation failed. Send preparation-summary.json only.' }
        foreach ($Required in @('Start-Observer-Capture.ps1', 'bootstrap-observer.py', 'private-baseline-receipt.json')) {
            if (-not (Test-Path -LiteralPath (Join-Path $Output $Required) -PathType Leaf)) {
                throw "Prepared artifact is missing: $Required. Nothing was started."
            }
        }
        if (-not (Test-Path -LiteralPath (Join-Path $Output 'frozen-source') -PathType Container)) {
            throw 'Prepared source folder is missing. Nothing was started.'
        }
        Write-Host 'PREPARED_ONLY; ATLAS_NOT_STARTED; NO_MARKET_DATA_OR_GROK_REQUESTS'
        Write-Host "LOCAL_START_SCRIPT=$(Join-Path $Output 'Start-Observer-Capture.ps1')"
        Write-Host 'The local start script is for a regular session only. Click normal START within 15 minutes; the five-minute capture begins at the first new scanner evaluation. This tool never auto-starts trading.'
    }
    finally { Pop-Location }
}
finally {
    & git -C $Repo worktree remove --force $Worktree
    if ($LASTEXITCODE -ne 0) { Write-Warning "Temporary source cleanup failed: $Worktree" }
}
