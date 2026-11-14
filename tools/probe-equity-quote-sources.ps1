param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [switch]$RecentRejections,
    [string[]]$Symbols
)
$ErrorActionPreference = 'Stop'
if ($RecentRejections -and $Symbols) { throw 'Choose recent rejections or explicit symbols.' }
if ($Symbols -and ($Symbols.Count -gt 2 -or ($Symbols | Where-Object { $_ -cnotmatch '^[A-Z][A-Z0-9.-]{0,9}$' }))) {
    throw 'Use at most two canonical equity symbols.'
}
if ($RecentRejections) {
    $Running = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'app\.gui\.app|bootstrap-observer\.py'
    }
    if (-not $Running) { throw 'Keep Atlas running for recent native rejection selection.' }
}
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'An exact reviewed commit is required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python is missing.' }
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision is outside the reviewed research branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\quote-source-probe-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
$Summary = Join-Path $Root 'summary.json'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Pinned probe checkout failed.' }
Push-Location $Source
try {
    & $Python -m pytest tests/asset_modules/test_equity_quote_source_probe.py --basetemp (Join-Path $Root 'checks') -q
    if ($LASTEXITCODE -ne 0) { throw 'Probe verification failed. No data requested.' }
    $ProbeArgs = @('-m','app.webull.equity_quote_source_probe','--repo',$Repo,'--output',$Summary,'--read')
    if ($RecentRejections) {
        $ProbeArgs += @('--checkpoint-directory',(Join-Path $env:TEMP 'atlas-performance'),'--rounds','2')
    } elseif ($Symbols) {
        $ProbeArgs += @('--symbols') + $Symbols + @('--rounds','2')
    }
    & $Python @ProbeArgs
    if ($LASTEXITCODE -ne 0) { throw 'Quote-source probe setup failed. No trading settings changed.' }
}
finally {
    Pop-Location
    & git -C $Repo worktree remove --force $Source
}
if (Test-Path -LiteralPath $Summary -PathType Leaf) {
    Write-Host "UPLOAD_SUMMARY=$Summary"
    Get-Content -LiteralPath $Summary -Raw
}
