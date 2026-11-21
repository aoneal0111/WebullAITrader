"""Offline NFI research with a pinned third-party exchange-info recording.

The recording is post-period test data, not verified January 2024 venue rules.
Every performance report must retain this limitation; no strategy promotion.
No Binance connection, proxy, new prices, order submission or fabricated rules.
"""
import argparse
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import traceback

try:
    from .nfi_runtime_preflight import cli_arguments, disconnected, validate
except ImportError:
    from nfi_runtime_preflight import cli_arguments, disconnected, validate

REPO = 'https://github.com/LuxAlgo/PineTS.git'
COMMIT = '100357e4297209e4693a75b3f56005ce6a73b259'
SOURCE_PATH = 'tests/compatibility/_data/api-exchangeInfo.json'
BLOB = '6059f36e6ba354ae81e080a4fa6883a0fc0f33c6'
SIZE = 15514816
SHA256 = '2485d9883294832ee8e382872bfe4e00207404041476fa1f4cd30344105010d7'
FILE = 'recorded-exchange-info.json'


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def load_metadata(path, pairs):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size != SIZE:
        raise ValueError('PINNED_METADATA_SIZE_REQUIRED')
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != SHA256:
        raise ValueError('PINNED_METADATA_IDENTITY_REQUIRED')
    data = json.loads(raw)
    symbols = data.get('symbols')
    if not isinstance(symbols, list) or not 160 <= len(symbols) <= 10000:
        raise ValueError('COMPLETE_RECORDED_SPOT_CATALOG_REQUIRED')
    by_pair = {}
    for s in symbols:
        p = s['baseAsset'] + '/' + s['quoteAsset']
        if p in by_pair:
            raise ValueError('DUPLICATE_RECORDED_MARKET')
        by_pair[p] = s
    if set(pairs) - by_pair.keys():
        raise ValueError('ALL_CONFIGURED_PAIRS_REQUIRE_RECORDED_METADATA')
    for pair in pairs:
        s = by_pair[pair]
        filters = {f['filterType'] for f in s.get('filters', [])}
        if not {'PRICE_FILTER', 'LOT_SIZE'} <= filters or not {'MIN_NOTIONAL', 'NOTIONAL'} & filters:
            raise ValueError('RECORDED_PRICE_SIZE_AND_NOTIONAL_RULES_REQUIRED')
    return data, {
        'source_repository': REPO, 'source_commit': COMMIT, 'source_path': SOURCE_PATH,
        'sha256': SHA256, 'recorded_server_time': datetime.fromtimestamp(data['serverTime'] / 1000, UTC).isoformat(),
        'source_class': 'THIRD_PARTY_POST_PERIOD_TEST_RECORDING',
        'historical_execution_rules_verified': False, 'qualification_eligible': False,
        'configured_pairs': list(pairs),
        'inactive_in_recording': [p for p in pairs if by_pair[p]['status'] != 'TRADING'],
        'limitations': ['Recording used by PineTS for offline compatibility tests; capture authenticity not independently attested',
            'December 2025 rules and active flags are not January 2024 historical rules',
            'Fee rates come from the native CCXT defaults, not verified historical account fees',
            'Do not classify this replay as reproduction of the author published win rate']}


def fetch(work):
    work = Path(work).resolve()
    target = work / FILE
    config = json.loads((work / 'config.json').read_text())
    if target.exists():
        load_metadata(target, config['exchange']['pair_whitelist'])
        return
    source = work / 'metadata-source'
    source.mkdir(exist_ok=False)
    def git(*args, stdout=None):
        r = subprocess.run(['git', '-c', 'core.longpaths=true', '-C', str(source), *args], stdout=stdout, check=False)
        if r.returncode:
            raise RuntimeError('PINNED_METADATA_FETCH_FAILED')
    git('init')
    git('remote', 'add', 'origin', REPO)
    git('fetch', '--depth=1', '--filter=blob:none', '--no-tags', 'origin', COMMIT)
    tree = subprocess.check_output(['git', '-c', 'core.longpaths=true', '-C', str(source), 'ls-tree', 'FETCH_HEAD', SOURCE_PATH], text=True)
    if tree.strip() != '100644 blob ' + BLOB + '\t' + SOURCE_PATH:
        raise ValueError('PINNED_METADATA_TREE_MISMATCH')
    temp = work / (FILE + '.partial')
    with temp.open('xb') as stream:
        # Read the verified object directly: revision:path can trigger Windows
        # filesystem probing against the deeply nested research directory.
        git('cat-file', 'blob', BLOB, stdout=stream)
    data, report = load_metadata(temp, config['exchange']['pair_whitelist'])
    temp.replace(target)
    write(work / 'metadata-context.json', report)
    print('RECORDED_METADATA_VERIFIED; SYMBOLS=' + str(len(data['symbols'])) + '; BYTES=' + str(SIZE), flush=True)


def exchange_from_recording(config, data):
    import ccxt
    from freqtrade.resolvers import ExchangeResolver
    parser = ccxt.binance({'options': {'fetchMarkets': {'types': ['spot']}, 'fetchCurrencies': False}})
    parser.publicGetExchangeInfo = lambda params={}: data
    parser.load_markets()
    exchange = ExchangeResolver.load_exchange(config, validate=False)
    try:
        exchange._markets = deepcopy(parser.markets)
        exchange._api.set_markets(parser.markets, parser.currencies)
        exchange._api_async.set_markets(parser.markets, parser.currencies)
        # Bound to this offline exchange instance; never query current live rules.
        exchange.reload_markets = lambda *a, **k: None
        exchange.validate_config(config)
    except Exception:
        exchange.close()
        raise
    return exchange


def replay(work, history, *, check_only=False, repair_timeframes=False):
    work, history = Path(work).resolve(), Path(history).resolve()
    status = {'scope': 'OFFLINE_NFI_RECORDED_METADATA_REPLAY_V1', 'status': 'FAILED',
        'network_requests': 0, 'orders': 0, 'qualification_eligible': False,
        'publication_reproduced': False, 'backtest_started': False}
    exchange = None
    try:
        with disconnected():
            validate(work, history)
            from freqtrade.commands.arguments import Arguments
            from freqtrade.configuration import Configuration
            from freqtrade.enums import RunMode
            from freqtrade.optimize.backtesting import Backtesting
            import ccxt
            config = Configuration(Arguments(cli_arguments(work, history)).get_parsed_arg(), RunMode.BACKTEST).get_config()
            data, metadata = load_metadata(work / FILE, config['exchange']['pair_whitelist'])
            write(work / 'metadata-context.json', metadata)
            exchange = exchange_from_recording(config, data)
            if repair_timeframes and not check_only:
                try:
                    from .nfi_timeframe_repair import prepare
                except ImportError:
                    from nfi_timeframe_repair import prepare
                derived, provenance = prepare(work, history, config['exchange']['pair_whitelist'])
                config['datadir'] = derived / 'binance'
                metadata['timeframe_repair'] = provenance
            bt = Backtesting(config, exchange=exchange)
            metadata.update(ccxt_version=ccxt.__version__, native_fee_per_side=bt.fee,
                            effective_pairlist=list(bt.pairlists.whitelist))
            write(work / 'metadata-context.json', metadata)
            status.update(ccxt_version=ccxt.__version__, native_fee_per_side=bt.fee,
                effective_pairlist=list(bt.pairlists.whitelist), metadata=metadata)
            if check_only:
                status['status'] = 'OFFLINE_ENGINE_READY'
            else:
                if list((work / 'backtest_results').glob('*.zip')):
                    raise ValueError('FRESH_NATIVE_RESULT_DIRECTORY_REQUIRED')
                status['backtest_started'] = True
                bt.start()
                if len(list((work / 'backtest_results').glob('*.zip'))) != 1:
                    raise ValueError('ONE_NATIVE_RESULT_ARCHIVE_REQUIRED')
                status['status'] = 'OFFLINE_RESEARCH_RESULT_READY'
    except Exception as exc:
        status['error_type'] = type(exc).__name__
        status['error'] = str(exc)
        (work / 'offline-error-trace.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
    finally:
        if exchange is not None:
            exchange.close()
        write(work / ('offline-engine-preflight.json' if check_only else 'offline-native-status.json'), status)
    return status


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, default=Path('/work'))
    p.add_argument('--history', type=Path, default=Path('/history'))
    p.add_argument('--fetch', action='store_true')
    p.add_argument('--check-only', action='store_true')
    p.add_argument('--repair-timeframes', action='store_true')
    a = p.parse_args()
    if a.fetch:
        fetch(a.work)
    else:
        report = replay(a.work, a.history, check_only=a.check_only, repair_timeframes=a.repair_timeframes)
        print('NFI_OFFLINE_REPLAY=' + report['status'])


if __name__ == '__main__':
    main()
