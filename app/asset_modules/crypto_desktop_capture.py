"""Prepare one manual visible crypto observation capture; never start Atlas."""
import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from .daily_research_journal import canonical
from .grok_observer_session import configuration_digest, source_manifest

ENV = 'ATLAS_CRYPTO_OBSERVATION_CAPTURE'
SCOPE = 'MANUAL_NATIVE_CRYPTO_OBSERVATION_V1'
SECONDS = 1800
FLAGS = {
    'WEBULL_TRADING_ENVIRONMENT': 'PAPER', 'LIVE_TRADING_ENABLED': 'false',
    'WARRIOR_FORWARD_PAPER_ENABLED': 'false', 'QUICK_SCALPER_ENABLED': 'false',
    'ATLAS_PAPER_CONTROLLER_ENABLED': 'true', 'ATLAS_CRYPTO_SHARED_ALLOCATION_ENABLED': 'true',
    'ATLAS_CRYPTO_NATIVE_OBSERVER_ENABLED': 'true', 'ATLAS_GROK_PAPER_ENTRIES_ENABLED': 'false',
    'ATLAS_DAILY_RESEARCH_ENABLED': 'true', 'ATLAS_CRYPTO_COMPLETED_BAR_CAPTURE_ENABLED': 'true',
    'ATLAS_EQUITY_PAPER_EXPERIMENT': '', 'ATLAS_GROK_EXECUTION_TRIAL': '',
    'CRYPTO_INTELLIGENCE_RESEARCH_ENABLED': 'true',
    'CRYPTO_DISCOVERY_ENABLED': 'true', 'CRYPTO_DISCOVERY_SYMBOLS': 'BTC/USD,ETH/USD',
    'CRYPTO_DISCOVERY_REFRESH_SECONDS': '60', 'CRYPTO_INTELLIGENCE_HISTORY_MAX_SYMBOLS': '3',
    'CRYPTO_INTELLIGENCE_HISTORY_BAR_COUNT': '64', 'CRYPTO_INTELLIGENCE_HISTORY_REQUEST_BUDGET': '4',
    'CRYPTO_CATALYST_ACQUISITION_ENABLED': 'false',
}


def validate(configuration):
    if (configuration.environment.value != 'PAPER' or configuration.live_trading_enabled
            or configuration.grok_paper_entries_enabled
            or not configuration.crypto_native_observer_enabled
            or not configuration.paper_controller_enabled
            or not configuration.crypto_shared_allocation_enabled
            or not configuration.daily_research_enabled
            or configuration.warrior_forward_paper_enabled or configuration.quick_scalper_enabled
            or not configuration.crypto_intelligence_enabled
            or not configuration.crypto_completed_bar_capture_enabled
            or not configuration.crypto_discovery_enabled
            or set(configuration.crypto_discovery_symbols) != {'BTC/USD', 'ETH/USD'}
            or configuration.crypto_catalyst_acquisition_enabled
            or os.environ.get('ATLAS_EQUITY_PAPER_EXPERIMENT', '').strip()
            or os.environ.get('ATLAS_GROK_EXECUTION_TRIAL', '').strip()):
        raise ValueError('CRYPTO_CAPTURE_REQUIRES_NATIVE_OBSERVATION_ONLY_FLAGS')


def load_request(path, configuration, source_hash, configuration_hash):
    path = Path(path).resolve()
    if path.stat().st_size > 8192:
        raise ValueError('CRYPTO_CAPTURE_REQUEST_BOUND')
    doc = json.loads(path.read_text(encoding='utf-8'))
    if (set(doc) != {'scope', 'id', 'created_at', 'seconds', 'source_sha256', 'configuration_sha256'}
            or doc['scope'] != SCOPE or doc['seconds'] != SECONDS
            or not isinstance(doc['id'], str) or len(doc['id']) != 32
            or doc['source_sha256'] != source_hash or doc['configuration_sha256'] != configuration_hash):
        raise ValueError('CRYPTO_CAPTURE_SOURCE_OR_CONFIGURATION_CHANGED')
    validate(configuration)
    if path.with_name('recording-session.json').exists():
        raise ValueError('CRYPTO_CAPTURE_ALREADY_USED_PREPARE_A_NEW_DIRECTORY')
    return path, doc


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def prepare(repo, output):
    from dotenv import dotenv_values
    from app.configuration import load_configuration
    repo, output = Path(repo).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('NEW_PRIVATE_CAPTURE_DIRECTORY_REQUIRED')
    if shutil.disk_usage(output.parent).free < 2*1024**3:
        raise ValueError('TWO_GIB_FREE_SPACE_REQUIRED')
    flags = dict(FLAGS, CRYPTO_DISCOVERY_PATH=str(output/'crypto-research.jsonl'))
    config = load_configuration({**dotenv_values(repo/'.env'), **os.environ, **flags})
    # validate() also observes the operator process's old lease variables. The
    # generated launcher explicitly clears these only in its own child scope.
    if (config.environment.value != 'PAPER' or config.live_trading_enabled
            or config.grok_paper_entries_enabled):
        raise ValueError('OBSERVATION_CONFIGURATION_INVALID')
    doc = dict(scope=SCOPE, id=uuid4().hex, created_at=datetime.now(UTC).isoformat(), seconds=SECONDS,
        source_sha256=sha256(canonical(source_manifest(repo))).hexdigest(),
        configuration_sha256=configuration_digest(config))
    output.mkdir(parents=True, exist_ok=False)
    request = output/'capture-request.json'
    request.write_text(json.dumps(doc, indent=2), encoding='utf-8')
    flags[ENV] = str(request)
    assignments='\n'.join('    '+quote(k)+' = '+quote(v) for k,v in flags.items())
    script="$ErrorActionPreference = 'Stop'\n"
    script+="$Running = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\\.exe$' -and $_.CommandLine -match 'app\\.gui\\.app|bootstrap-observer\\.py|grok_day_proposals' }\n"
    script+="if ($Running) { throw 'Close the existing Atlas and Grok intentions normally first. Nothing started.' }\n"
    script+="if (Test-Path -LiteralPath "+quote(output/'recording-session.json')+") { throw 'This capture was already used. Prepare a new directory to preserve its history.' }\n"
    script+="$Flags = @{\n"+assignments+"\n}\n$Before = @{}\n"
    script+="$Pushed = $false\ntry {\nforeach ($Name in $Flags.Keys) { $Before[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process'); [Environment]::SetEnvironmentVariable($Name, $Flags[$Name], 'Process') }\n"
    script+="Push-Location "+quote(repo)+"\n$Pushed = $true\n"
    script+="    Write-Host 'VISIBLE ATLAS; CRYPTO OBSERVATION ONLY; SELECT CRYPTO AND ACTIVATE IT MANUALLY; NO ENTRY AUTHORITY OR GROK REQUESTS'\n"
    script+="    & "+quote(repo/'.venv/Scripts/python.exe')+" -m app.gui.app\n"
    script+="    if ($LASTEXITCODE -ne 0) { throw 'Atlas exited with a diagnostic. Keep this capture directory.' }\n"
    script+="} finally { foreach ($Name in $Before.Keys) { [Environment]::SetEnvironmentVariable($Name, $Before[$Name], 'Process') }; if ($Pushed) { Pop-Location } }\n"
    launcher = output/'Start-Atlas-Crypto-Observation.ps1'
    launcher.write_text(script, encoding='utf-8')
    report=dict(scope='CRYPTO_DESKTOP_OBSERVATION_PREPARATION_V1', prepared_only=True,
        atlas_started=False, network_requests=0, grok_requests=0, orders=0,
        entries_enabled=False, capture_seconds=SECONDS, source_sha256=doc['source_sha256'],
        configuration_sha256=doc['configuration_sha256'],
        limitations=['Manual visible desktop activation required; no desktop success is attested by preparation.',
            'Thirty-minute recording begins at desktop composition, not the first signal.',
            'Recording expiry never closes Atlas or disables independent position protection.',
            'Observed decisions are not simulated trades or a profitability backtest.'])
    (output/'preparation-summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('CAPTURE_DIRECTORY='+str(output));print('MANUAL_ATLAS='+str(launcher))
    print('PREPARED_ONLY; ATLAS_NOT_STARTED; NO_API_REQUESTS; NO_ENV_FILE_CHANGED')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True);p.add_argument('--output', type=Path, required=True)
    args=p.parse_args();prepare(args.repo,args.output)


if __name__ == '__main__':
    main()
