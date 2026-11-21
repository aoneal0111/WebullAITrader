param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Trial
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Trial = (Resolve-Path -LiteralPath $Trial).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Before = git -C $Repo rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Installed revision unavailable.' }
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Probe revision is not on the reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\grok-reasoning-source-' + [guid]::NewGuid().ToString('N'))
git -C $Repo worktree add --detach $Root $Revision
if ($LASTEXITCODE -ne 0) { throw 'Pinned probe checkout failed.' }
Push-Location $Root
try {
    & $Python -m pytest tests/asset_modules/test_grok_reasoning_probe.py tests/asset_modules/test_grok.py tests/asset_modules/test_grok_request_diagnostics.py tests/asset_modules/test_grok_execution_trial.py tests/asset_modules/test_grok_compact_review.py -q --basetemp (Join-Path $Root 'probe-checks')
    if ($LASTEXITCODE -ne 0) { throw 'Offline probe checks failed. No paid request was made.' }
    Write-Host 'ONE PAID HISTORICAL LOW-REASONING REQUEST; ORIGINAL SHARED BUDGET; NO ORDER PATH; ATLAS LEFT RUNNING'
    & $Python -m app.asset_modules.grok_reasoning_probe --repo $Repo --trial $Trial
    if ($LASTEXITCODE -ne 0) { throw 'Probe could not run. No renewal, fallback or automatic retry was attempted.' }
} finally { Pop-Location }
$After = git -C $Repo rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $After -cne $Before) { throw 'Installed revision changed during probe; review the reported result.' }
Write-Host 'PROBE_FINISHED; INSTALLED_SOURCE_UNCHANGED; NO_ATLAS_START_STOP; NO_SETTINGS_CHANGED'
