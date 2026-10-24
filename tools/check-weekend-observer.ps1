param([string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'), [string]$Revision, [switch]$CryptoRead, [switch]$CryptoReliability, [switch]$CryptoBars, [switch]$CryptoWorker, [switch]$CryptoProviderWorker, [switch]$GrokContext)
$ErrorActionPreference = 'Stop'
if ($CryptoBars -and ($CryptoRead -or $CryptoReliability)) { throw 'Choose one crypto check mode.' }
if ($CryptoWorker -and ($CryptoRead -or $CryptoReliability -or $CryptoBars -or $CryptoProviderWorker)) { throw 'The disconnected worker check cannot be combined with online reads.' }
if ($CryptoProviderWorker -and ($CryptoRead -or $CryptoReliability -or $CryptoBars)) { throw 'Choose one online crypto check mode.' }
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
        & $Python -m pytest tests/asset_modules/test_grok_observer.py tests/asset_modules/test_grok_observer_input.py tests/crypto_research/test_snapshot_readiness.py tests/crypto_research/test_readiness.py tests/crypto_research/test_reliability.py tests/crypto_research/test_bar_alignment.py --basetemp (Join-Path $Root 'tests') -q
        if ($LASTEXITCODE -ne 0) { throw 'Observer fixture tests failed.' }
        & $Python -m app.asset_modules.grok_observer
        if ($LASTEXITCODE -ne 0) { throw 'Disconnected observer check failed.' }
        Write-Host 'READY=OFFLINE_FIXTURES_ONLY; ONLINE_TRIAL=NOT_ENABLED; BASELINE=UNCHANGED'
        if ($GrokContext) {
            & $Python -m pytest tests/asset_modules/test_grok_observer_context.py --basetemp (Join-Path $Root 'context-tests') -q
            if ($LASTEXITCODE -ne 0) { throw 'Observer strategy-context tests failed.' }
            & $Python -m app.asset_modules.grok_observer_context
            if ($LASTEXITCODE -ne 0) { throw 'Disconnected context fixtures failed.' }
            Write-Host 'CONTEXT_READY=OFFLINE_FIXTURES_ONLY; ONLINE_TRIAL=NOT_ENABLED; BASELINE=UNCHANGED'
        }
        if ($CryptoProviderWorker) {
            & $Python -m pytest tests/crypto_research/test_provider_worker.py tests/asset_modules/test_crypto_worker.py --basetemp (Join-Path $Root 'provider-worker-tests') -q
            if ($LASTEXITCODE -ne 0) { throw 'Provider worker fixture tests failed.' }
        }
        if ($CryptoWorker) {
            & $Python -m pytest tests/asset_modules/test_crypto_worker.py tests/asset_modules/test_crypto_durable_intents.py tests/asset_modules/test_crypto_allocation.py tests/crypto_research/test_worker_readiness.py --basetemp (Join-Path $Root 'worker-tests') -q
            if ($LASTEXITCODE -ne 0) { throw 'Disconnected worker and ledger tests failed.' }
            $Desktop = [Environment]::GetFolderPath('Desktop')
            if (-not $Desktop) { $Desktop = $env:USERPROFILE }
            $Output = Join-Path $Desktop ('atlas-crypto-worker-' + [guid]::NewGuid().ToString('N') + '.json')
            & $Python -m app.crypto_research.worker_readiness --output $Output
            $WorkerExit = $LASTEXITCODE
            Write-Host "UPLOAD_SUMMARY=$Output"
            if (Test-Path -LiteralPath $Output -PathType Leaf) { & explorer.exe "/select,`"$Output`"" }
            if ($WorkerExit -ne 0) { throw 'Synthetic worker check failed; no installed trading settings changed.' }
        }
        if ($CryptoRead -or $CryptoReliability -or $CryptoBars -or $CryptoProviderWorker) {
            $Desktop = [Environment]::GetFolderPath('Desktop')
            if (-not $Desktop) { $Desktop = $env:USERPROFILE }
            $Prefix = if ($CryptoProviderWorker) { 'atlas-crypto-provider-worker-' } elseif ($CryptoBars) { 'atlas-crypto-bars-' } elseif ($CryptoReliability) { 'atlas-crypto-reliability-' } else { 'atlas-crypto-readiness-' }
            $Output = Join-Path $Desktop ($Prefix + [guid]::NewGuid().ToString('N') + '.json')
            $ReadArgs = @('-m','app.crypto_research.readiness','--repo',$Repo,'--output',$Output,'--online-read')
            if ($CryptoReliability) {
                Write-Host 'Starting approximately five minutes of crypto data reads. No Atlas launch, Grok requests or orders.'
                $ReadArgs += '--reliability'
            }
            if ($CryptoBars) {
                Write-Host 'Starting approximately two minutes of completed-bar and fresh-quote reads. No orders or Grok requests.'
                $ReadArgs += '--bar-alignment'
            }
            if ($CryptoProviderWorker) {
                Write-Host 'Starting approximately two minutes of actual crypto inputs through an isolated worker. Temporary entries stay disabled; no broker orders or Grok requests.'
                $ReadArgs += '--provider-worker'
            }
            & $Python @ReadArgs
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
