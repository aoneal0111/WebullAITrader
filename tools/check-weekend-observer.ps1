param([string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'), [string]$Revision, [switch]$CryptoRead)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Atlas Python is missing.' }
if (-not $Revision) {
    & git -C $Repo fetch origin research/high-win-rate-comparison
    if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
    $Revision = (& git -C $Repo rev-parse FETCH_HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Revision lookup failed.' }
}
$Root = Join-Path $env:TEMP ('atlas-observer-check-' + [guid]::NewGuid().ToString('N'))
$Worktree = Join-Path $Root 'source'
$null = New-Item -ItemType Directory -Path $Root
& git -C $Repo worktree add --detach $Worktree $Revision
if ($LASTEXITCODE -ne 0) { throw 'Source checkout failed.' }
try {
    Push-Location $Worktree
    try {
        # Parse local settings only. No provider construction, credentials output,
        # runtime start, persistence stores or order services.
        $Check = @'
import json, os, sys
from pathlib import Path
from dotenv import dotenv_values
from app.configuration.loader import load_configuration
root = Path(sys.argv[1])
env = {**dotenv_values(root / '.env'), **os.environ}
c = load_configuration(env)
safe = c.environment.value == 'PAPER' and not c.live_trading_enabled and not c.grok_paper_entries_enabled
print(json.dumps(dict(scope='LOCAL_FLAGS_NO_NETWORK', environment=c.environment.value,
    live_trading_enabled=c.live_trading_enabled, grok_order_review_enabled=c.grok_paper_entries_enabled,
    flags_ready_for_observation=safe, credentials_validated_online=False), indent=2))
raise SystemExit(0 if safe else 2)
'@
        $Check | & $Python - $Repo
        if ($LASTEXITCODE -ne 0) { throw 'Observation flags are not safe. No settings were changed.' }
        & $Python -m pytest tests/asset_modules/test_grok_observer.py tests/asset_modules/test_grok_observer_input.py tests/crypto_research/test_snapshot_readiness.py tests/crypto_research/test_readiness.py --basetemp (Join-Path $Root 'tests') -q
        if ($LASTEXITCODE -ne 0) { throw 'Observer fixture tests failed.' }
        & $Python -m app.asset_modules.grok_observer
        if ($LASTEXITCODE -ne 0) { throw 'Disconnected observer check failed.' }
        Write-Host 'READY=OFFLINE_FIXTURES_ONLY; ONLINE_TRIAL=NOT_ENABLED; BASELINE=UNCHANGED'
        if ($CryptoRead) {
            $Desktop = [Environment]::GetFolderPath('Desktop')
            if (-not $Desktop) { $Desktop = $env:USERPROFILE }
            $Output = Join-Path $Desktop ('atlas-crypto-readiness-' + [guid]::NewGuid().ToString('N') + '.json')
            & $Python -m app.crypto_research.readiness --repo $Repo --output $Output --online-read
            $ReadExit = $LASTEXITCODE
            if (Test-Path -LiteralPath $Output -PathType Leaf) { & explorer.exe "/select,`"$Output`"" }
            if ($ReadExit -ne 0) { throw 'Crypto reads did not pass. Send the small readiness JSON; no trading settings changed.' }
        }
    }
    finally { Pop-Location }
}
finally {
    & git -C $Repo worktree remove --force $Worktree
    if ($LASTEXITCODE -ne 0) { Write-Warning "Temporary source cleanup failed: $Worktree" }
}
