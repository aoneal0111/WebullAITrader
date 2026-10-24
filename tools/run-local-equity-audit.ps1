param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [string]$ResearchRoot,
    [string]$Revision,
    [string]$OutputFile,
    [ValidateSet('AuditV1','ResearchV2')][string]$Mode = 'AuditV1'
)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python is missing.' }

if (-not $ResearchRoot) {
    $Bases = @(
        [Environment]::GetFolderPath('Desktop')
        [Environment]::GetFolderPath('MyDocuments')
        (Join-Path $env:USERPROFILE 'Downloads')
        (Join-Path $env:USERPROFILE 'OneDrive')
        $Repo
        $env:OneDrive
        $env:OneDriveCommercial
        $env:TEMP
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) } | Sort-Object -Unique
    $Folders = @(
        foreach ($Base in $Bases) {
            Get-ChildItem -LiteralPath $Base -Directory -Filter 'atlas-temporal-*' -Depth 4 -ErrorAction SilentlyContinue
        }
    ) | Sort-Object FullName -Unique
    $Candidates = @(
        foreach ($Folder in $Folders) {
            $History = Join-Path $Folder.FullName 'history'
            $Required = @((Join-Path $Folder.FullName 'report.json'), (Join-Path $History 'manifest.json'))
            foreach ($Symbol in @('SPY','QQQ','AAPL','MSFT','NVDA','TSLA')) {
                $Required += Join-Path $History ($Symbol + '-normalized.json')
            }
            $Missing = @($Required | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
            if ($Missing.Count -eq 0) { $Folder }
        }
    ) | Sort-Object LastWriteTimeUtc -Descending
    if ($Candidates.Count -eq 0) {
        throw 'No complete local history/report folder was found. Pass -ResearchRoot with its actual location. No history was downloaded.'
    }
    $ResearchRoot = $Candidates[0].FullName
}
$ResearchRoot = (Resolve-Path -LiteralPath $ResearchRoot).Path
$History = Join-Path $ResearchRoot 'history'
$Report = Join-Path $ResearchRoot 'report.json'
Write-Host "LOCAL_RESEARCH=$ResearchRoot"

if (-not $OutputFile) {
    $Desktop = [Environment]::GetFolderPath('Desktop')
    if (-not $Desktop) { $Desktop = $env:USERPROFILE }
    $Prefix = if ($Mode -eq 'ResearchV2') { 'atlas-v2-research-' } else { 'atlas-local-audit-' }
    $OutputFile = Join-Path $Desktop ($Prefix + [guid]::NewGuid().ToString('N') + '.json')
}
$OutputFile = [IO.Path]::GetFullPath($OutputFile)
if (Test-Path -LiteralPath $OutputFile) { throw 'Output exists; use a new filename.' }

if (-not $Revision) {
    & git -C $Repo fetch origin research/high-win-rate-comparison
    if ($LASTEXITCODE -ne 0) { throw 'Research fetch failed.' }
    $Revision = (& git -C $Repo rev-parse FETCH_HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Revision lookup failed.' }
}
$SourceRoot = Join-Path $env:TEMP ('atlas-local-audit-source-' + [guid]::NewGuid().ToString('N'))
$Worktree = Join-Path $SourceRoot 'review-source'
$null = New-Item -ItemType Directory -Path $SourceRoot
& git -C $Repo worktree add --detach $Worktree $Revision
if ($LASTEXITCODE -ne 0) { throw 'Review checkout failed.' }

try {
    Push-Location $Worktree
    try {
        $TestTemp = Join-Path $SourceRoot 'tests'
        $Test = if ($Mode -eq 'ResearchV2') { 'tests/equity_overhaul/test_research_v2.py' } else { 'tests/equity_overhaul/test_local_audit.py' }
        $Tool = if ($Mode -eq 'ResearchV2') { 'tools/screen-equity-v2.py' } else { 'tools/audit-equity-local-history.py' }
        & $Python -m pytest $Test --basetemp $TestTemp -q
        if ($LASTEXITCODE -ne 0) { throw 'Local audit verification failed.' }
        & $Python $Tool --history $History --report $Report --output $OutputFile
        $AuditExit = $LASTEXITCODE
        if (Test-Path -LiteralPath $OutputFile -PathType Leaf) {
            Write-Host "UPLOAD_SUMMARY=$OutputFile"
            & explorer.exe "/select,`"$OutputFile`""
        }
        if ($AuditExit -ne 0) {
            throw 'Audit found an input failure or replay mismatch. Send the small summary if produced, plus status output. Raw history is unchanged.'
        }
    }
    finally { Pop-Location }
}
finally {
    # Only this script's disposable source checkout is removed.
    & git -C $Repo worktree remove --force $Worktree
    if ($LASTEXITCODE -ne 0) { Write-Warning "Temporary source cleanup failed: $Worktree" }
}
