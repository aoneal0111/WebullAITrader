"""Run only native backtesting after a matching offline preflight."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

try:
    from .nfi_runtime_preflight import cli_arguments
except ImportError:  # Standalone script mounted in the isolated container.
    from nfi_runtime_preflight import cli_arguments


def run(work, history, *, execute=subprocess.run):
    work, history = Path(work).resolve(), Path(history).resolve()
    preflight = json.loads((work / 'runtime-preflight.json').read_text())
    if (preflight.get('status') != 'RUNTIME_CONFIG_OK'
        or preflight.get('config_sha256') != sha256((work / 'config.json').read_bytes()).hexdigest()
        or preflight.get('strategy_sha256') != sha256((work / 'strategies/NostalgiaForInfinityX8.py').read_bytes()).hexdigest()
        or preflight.get('resolved_datadir') != str(history / 'binance')):
        raise ValueError('MATCHING_RUNTIME_PREFLIGHT_REQUIRED')
    results = work / 'backtest_results'
    if list(results.glob('*.zip')):
        raise ValueError('FRESH_RESULT_DIRECTORY_REQUIRED')
    result = execute([sys.executable, '-m', 'freqtrade', *cli_arguments(work, history)], check=False)
    archives = list(results.glob('*.zip'))
    status = ('NATIVE_PROCESS_FAILED' if result.returncode else
              'NATIVE_RESULT_READY' if len(archives) == 1 else 'MISSING_OR_AMBIGUOUS_NATIVE_RESULT')
    report = {'scope': 'NFI_NATIVE_RUN_STATUS_V1', 'status': status,
              'returncode': result.returncode, 'result_archives': len(archives), 'orders': 0}
    (work / 'native-run-status.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, default=Path('/work'))
    p.add_argument('--history', type=Path, default=Path('/history'))
    a = p.parse_args()
    report = run(a.work, a.history)
    print('NFI_NATIVE_RUN=' + report['status'])
    if report['status'] != 'NATIVE_RESULT_READY':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
