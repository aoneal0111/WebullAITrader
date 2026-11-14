"""Prepare unmodified NFI with upstream spot configuration; no bot or wrapper.

This is a new source-faithful run, not proof that an older published summary
used this strategy revision, image, data or configuration.
"""
import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import zipfile

COMMIT = 'bbddbd94f97f3c14336387d2958183870891b951'
IMAGE = 'freqtradeorg/freqtrade:2026.9@sha256:989aff5636d8f0213ed44fe334c3314a194a51c0c755d16866960247aafd2eae'
HASHES = {
    'NostalgiaForInfinityX8.py': '80f824e879493babf42cb861d751aa077bcc475ccacf08ae62ebf7abd26eeb8f',
    'LICENSE': '3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986',
    'configs/exampleconfig.json': '707a8eeca4395f7b69b9ea0993f4eb6c031839f26ea22a4254db28c01f179da0',
    'configs/exampleconfig_secret.json': 'f70df807e3e94741290de5a3a5893330694cb2a41cf66712c6dd193ace116386',
    'configs/trading_mode-spot.json': 'd690089865d32b634f7840611ad2009db72f5582697c4365b1fd5ff7ebf93bd4',
    'configs/blacklist-binance.json': 'ffb483d66fab24379723b311f3470cf3b58349a1aa95207cb02788726c6482ac',
    'configs/pairlist-backtest-static-binance-spot-usdt.json': '0f81d7ce509094f453fad3dec6ed40c9bae15d4a8326244521676c2a1be713a5',
    'configs/proxy-binance.json': 'cb2fc725edc64203c8a12999ed98225036a29f13b633c45bba8a7f876696f4c6',
    'tests/backtests/helpers.py': 'a88459c160644129cda8923679991eff14c25d5eb78cb7623e2ad16764e44e41',
    'tests/backtests/test_winrate_and_drawdown.py': '7e229dbfba158cd1821ecd19204bf730e05810194c1e9a7e47f1170640655a02',
}
CONFIGS = list(HASHES)[2:8]


def digest(path):
    h = sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def jsonc(text):
    # Keep quoted strings (including URLs) intact while removing JSON comments.
    pattern = r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/'
    return json.loads(re.sub(pattern, lambda m: m[0] if m[0].startswith('"') else '', text))


def merge(a, b):
    for key, value in b.items():
        if isinstance(value, dict) and isinstance(a.get(key), dict):
            merge(a[key], value)
        else:
            a[key] = value
    return a


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def isolated_config(source):
    source = Path(source)
    config = {}
    for name in CONFIGS:
        merge(config, jsonc((source / name).read_text(encoding='utf-8')))
    pairs = config['exchange']['pair_whitelist']
    if len(pairs) < 40 or config['max_open_trades'] != 6 or config['stake_amount'] != 'unlimited':
        raise ValueError('UPSTREAM_CONFIG_MISMATCH')
    # API/telegram and forced entries are irrelevant to backtesting. Disable them
    # without overriding stoploss, stake, grinding, sizing, entry or exit rules.
    # Disabled nested sections still have required schema fields. Preserve the
    # upstream blank token/chat_id and API fields rather than replacing objects.
    config['api_server']['enabled'] = False
    config['telegram']['enabled'] = False
    config.update(dry_run=True, force_entry_enable=False, user_data_dir='/work',
                  strategy_path='/work/strategies', datadir='/history/binance')
    for name in ('key', 'secret', 'password', 'walletAddress', 'privateKey'):
        if config['exchange'].get(name):
            raise ValueError('UPSTREAM_CONFIG_CONTAINS_CREDENTIALS')
    return config


def prepare(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    for name, expected in HASHES.items():
        path = source / name
        if path.is_symlink() or digest(path) != expected:
            raise ValueError('PINNED_SOURCE_MISMATCH:' + name)
    config = isolated_config(source)
    pairs = config['exchange']['pair_whitelist']
    output.mkdir(parents=True, exist_ok=False)
    (output / 'strategies').mkdir()
    for name in ('NostalgiaForInfinityX8.py', 'LICENSE'):
        shutil.copyfile(source / name, output / 'strategies' / name)
    write(output / 'config.json', config)
    manifest = {'scope': 'UNMODIFIED_NFI_SOURCE_BACKTEST_V1', 'source_commit': COMMIT,
        'source_hashes': HASHES, 'image': IMAGE, 'prepared_at': datetime.now(UTC).isoformat(),
        'prepared_hashes': {name: digest(output / name) for name in
            ('strategies/NostalgiaForInfinityX8.py', 'strategies/LICENSE', 'config.json')},
        'pairs': pairs, 'timeframes': ['5m', '15m', '1h', '4h', '1d'],
        'execution_mode': 'BACKTEST_ONLY', 'wrapper': None,
        'publication_reproduced': False, 'lookahead_checked': False,
        'upstream_ci_runtime_matched': False,
        'limitations': ['Upstream CI uses mutable latest image and market-data tags; exact old report provenance unresolved',
            'Pinned official Freqtrade 2026.9 runtime differs from unspecified upstream CI runtime',
            'Default run is January 2024, matching an available report window, not the aggregate summary',
            'Backtest candles are not observed spread, order queue, slippage or live fills',
            'Static universe and historical data coverage must be reviewed before interpreting results',
            'Monthly upstream pytest threshold warnings are not performance assertions']}
    write(output / 'source-run-manifest.json', manifest)
    return manifest


def history_check(work, history):
    work, history = Path(work), Path(history).resolve()
    m = json.loads((work / 'source-run-manifest.json').read_text())
    for name, expected in m['prepared_hashes'].items():
        path = work / name
        if path.is_symlink() or digest(path) != expected:
            raise ValueError('PREPARED_SOURCE_OR_CONFIG_CHANGED')
    rows, missing, total_bytes = {}, [], 0
    # Every configured pair is retained. Do not silently shrink to survivors.
    for pair in sorted(set(m['pairs']) | {'BTC/USDT'}):
        for tf in m['timeframes']:
            path = history / 'binance' / (pair.replace('/', '_') + '-' + tf + '.feather')
            if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
                missing.append(path.name)
            else:
                total_bytes += path.stat().st_size
                if path.stat().st_size > 2 * 1024 ** 3 or total_bytes > 64 * 1024 ** 3:
                    raise ValueError('HISTORY_HASH_SCAN_SIZE_BOUND_EXCEEDED')
                rows[path.name] = {'sha256': digest(path), 'bytes': path.stat().st_size}
                if len(rows) % 50 == 0:
                    print(f'NFI_HISTORY_HASHED_FILES={len(rows)}; BYTES={total_bytes}', flush=True)
    result = {'scope': 'NFI_SOURCE_HISTORY_INVENTORY_V1',
        'status': 'FILES_PRESENT_CONTENT_COVERAGE_NOT_YET_CHECKED' if not missing else 'MISSING_FILES',
        'files': rows, 'missing': missing, 'orders': 0,
        'limitation': 'File presence and hashes do not establish warmup or period coverage; review native loader warnings.'}
    write(work / 'history-inventory.json', result)
    return result


def repair_prepare(source, previous_work, output):
    previous_work = Path(previous_work)
    manifest = json.loads((previous_work / 'source-run-manifest.json').read_text())
    if manifest.get('source_commit') != COMMIT or manifest.get('scope') != 'UNMODIFIED_NFI_SOURCE_BACKTEST_V1':
        raise ValueError('EXACT_PREVIOUS_SOURCE_RUN_REQUIRED')
    for name, expected in manifest['prepared_hashes'].items():
        path = previous_work / name
        if path.is_symlink() or digest(path) != expected:
            raise ValueError('PREVIOUS_SOURCE_RUN_CHANGED')
    before = json.loads((previous_work / 'config.json').read_text())
    after = isolated_config(source)
    omit = lambda c: {k: v for k, v in c.items() if k not in {'telegram', 'api_server'}}
    if omit(before) != omit(after):
        raise ValueError('REPAIR_MUST_PRESERVE_TRADING_CONFIGURATION')
    if digest(previous_work / 'strategies/NostalgiaForInfinityX8.py') != HASHES['NostalgiaForInfinityX8.py']:
        raise ValueError('PREVIOUS_STRATEGY_CHANGED')
    return prepare(source, output)


def summarize(work):
    work = Path(work)
    archives = list((work / 'backtest_results').glob('*.zip'))
    if len(archives) != 1:
        raise ValueError('ONE_NATIVE_RESULT_ARCHIVE_REQUIRED')
    with zipfile.ZipFile(archives[0]) as z:
        candidates = [i for i in z.infolist() if i.filename.endswith('.json')
                      and '_config' not in i.filename and '.meta.' not in i.filename]
        if len(candidates) != 1 or candidates[0].file_size > 64 * 1024 * 1024:
            raise ValueError('BOUNDED_NATIVE_RESULT_REQUIRED')
        result = json.loads(z.read(candidates[0]))['strategy']['NostalgiaForInfinityX8']
    trades = result['trades']
    wins = sum(t['profit_abs'] > 0 for t in trades)
    losses = sum(t['profit_abs'] < 0 for t in trades)
    net = sum(t['profit_abs'] for t in trades)
    if result['total_trades'] != len(trades) or wins != result['wins'] or losses != result['losses']:
        raise ValueError('NATIVE_TRADE_COUNTS_DO_NOT_RECONCILE')
    if abs(net - result['profit_total_abs']) > .01:
        raise ValueError('NATIVE_TRADE_PNL_DOES_NOT_RECONCILE')
    summary = {'scope': 'UNMODIFIED_NFI_NEW_RUN_RESULT_V1', 'source_commit': COMMIT,
        'archive_sha256': digest(archives[0]), 'closed_trades': len(trades),
        'wins': wins, 'losses': losses, 'draws': len(trades) - wins - losses,
        'win_rate': wins / len(trades) if trades else None, 'simulated_net_pnl': net,
        'profit_factor': result.get('profit_factor'), 'max_drawdown_account': result.get('max_drawdown_account'),
        'backtest_start': result.get('backtest_start'), 'backtest_end': result.get('backtest_end'),
        'native_fee_fields': {k: result.get(k) for k in ('fee_open', 'fee_close')},
        'reported_metrics': {k: result.get(k) for k in ('starting_balance', 'final_balance', 'rejected_signals')},
        'orders': 0, 'publication_reproduced': False, 'strategy_promotion': 'NONE',
        'status': 'NEW_BACKTEST_REVIEW_REQUIRED' if trades else 'NO_TRADE_EVIDENCE',
        'limitations': ['Source-faithful new run; exact published summary provenance unresolved',
            'Native exported fee fields may be absent; inspect individual trade fees and resolved config',
            'Missing candles, warmup, lookahead and recursive stability require separate review']}
    if (work / 'metadata-context.json').is_file():
        metadata = json.loads((work / 'metadata-context.json').read_text())
        summary.update(scope='NFI_OFFLINE_POST_PERIOD_METADATA_RESEARCH_V1',
                       market_metadata=metadata, qualification_eligible=False)
        summary['limitations'].extend(metadata['limitations'])
    write(work / 'reproduction-summary.json', summary)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path)
    p.add_argument('--previous-work', type=Path)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--history', type=Path)
    p.add_argument('--summarize', action='store_true')
    a = p.parse_args()
    if a.summarize:
        result = summarize(a.work)
    elif a.history:
        result = history_check(a.work, a.history)
        if result['missing']:
            print('NFI_HISTORY=MISSING_FILES; SEE=history-inventory.json')
            raise SystemExit(2)
    elif a.source and a.previous_work:
        result = repair_prepare(a.source, a.previous_work, a.work)
    elif a.source:
        result = prepare(a.source, a.work)
    else:
        p.error('--source, --history or --summarize required')
    print('NFI_SOURCE_STATUS=' + result.get('status', 'PREPARED_NOT_STARTED'))


if __name__ == '__main__':
    main()
