param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Revision,
    [Parameter(Mandatory=$true)][string]$Trial,
    [string]$Ledger = ''
)
$ErrorActionPreference = 'Stop'
if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'Exact reviewed revision required.' }
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Trial = (Resolve-Path -LiteralPath $Trial).Path
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not $Ledger) {
    $Lookup = @'
import json, os, sys
from pathlib import Path
repo, trial = (Path(p).resolve() for p in sys.argv[1:3])
os.chdir(repo)
sys.path.insert(0, str(repo))
from dotenv import dotenv_values
from app.configuration.loader import load_configuration
from app.asset_modules.paper_day_setup import FLAGS
from app.asset_modules.grok_observer_session import configuration_digest
flags = dict(FLAGS, ATLAS_EQUITY_PAPER_EXPERIMENT=str(trial/'experiment.json'),
    ATLAS_LEARNING_OUTCOME_JOURNAL_PATH=str(trial/'learning-outcomes.json'),
    ATLAS_GROK_EXECUTION_TRIAL='', ATLAS_CRYPTO_OBSERVATION_CAPTURE='',
    ATLAS_CRYPTO_NATIVE_OBSERVER_ENABLED='false')
spec = json.loads((trial/'experiment.json').read_text(encoding='utf-8-sig'))
if spec.get('profile') in ('GROK_WARRIOR_V5','GROK_WARRIOR_ACCOUNT_V6'):
    flags['ATLAS_PAPER_CONTROLLER_ENABLED'] = 'true'
config = load_configuration({**dotenv_values(repo/'.env'), **os.environ, **flags})
pointer = json.loads((trial/'recording-session.json').read_text())
manifest = json.loads((Path(pointer['session'])/'research-manifest.json').read_text())
if pointer.get('trial_id') != spec.get('trial_id') or manifest.get('paper_experiment') != spec:
    raise ValueError('NATIVE_RECORDING_TRIAL_MISMATCH')
if configuration_digest(config) != manifest['configuration_sha256']:
    raise ValueError('NATIVE_CONFIGURATION_CHANGED_PASS_EXACT_LEDGER_EXPLICITLY')
print(Path(config.execution_database_path).with_name('paper-execution.sqlite3'))
'@
    $Result = @($Lookup | & $Python - $Repo $Trial)
    if ($LASTEXITCODE -ne 0 -or $Result.Count -ne 1) { throw 'Native ledger resolution failed; no settings changed.' }
    $Ledger = [string]$Result[0]
}
$Ledger = (Resolve-Path -LiteralPath $Ledger).Path
& git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw 'Fetch failed.' }
& git -C $Repo merge-base --is-ancestor $Revision FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw 'Revision outside reviewed branch.' }
$Root = Join-Path $env:LOCALAPPDATA ('AtlasResearch\closed-paper-day-review-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Root
$Source = Join-Path $Root 'review-source'
& git -C $Repo worktree add --detach $Source $Revision
if ($LASTEXITCODE -ne 0) { throw 'Read-only review checkout failed.' }
Push-Location $Source
try {
    & $Python -m unittest tests.strategy_research.test_paper_day_review_stdlib tests.strategy_research.test_audit_stdlib -v
    if ($LASTEXITCODE -ne 0) { throw 'Fill reconciliation/export checks failed. No source database changed.' }
    & $Python -m app.strategy_research.paper_day_review --trial $Trial --ledger $Ledger --output (Join-Path $Root 'export')
    if ($LASTEXITCODE -ne 0) { throw 'Review export failed. Preserve the error; do not rerun Atlas.' }
} finally { Pop-Location }
Invoke-Item (Join-Path $Root 'export')
