"""Network-disabled configuration/strategy preflight in Freqtrade 2026.9.

Uses the same parsed CLI arguments as the backtest, including explicit datadir.
No exchange is constructed and no historical simulation is started here.
"""
import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
from pathlib import Path
import socket


@contextmanager
def disconnected():
    def forbidden(*args, **kwargs):
        raise RuntimeError('NETWORK_DISABLED_DURING_NFI_PREFLIGHT')
    originals = [(socket.socket, name, getattr(socket.socket, name))
                 for name in ('connect', 'connect_ex', 'sendto')]
    originals.append((socket, 'getaddrinfo', socket.getaddrinfo))
    try:
        for obj, name, _ in originals:
            setattr(obj, name, forbidden)
        yield
    finally:
        for obj, name, value in originals:
            setattr(obj, name, value)


def cli_arguments(work, history):
    return ['backtesting', '--config', str(work / 'config.json'),
        '--strategy', 'NostalgiaForInfinityX8', '--strategy-path', str(work / 'strategies'),
        '--user-data-dir', str(work), '--datadir', str(history / 'binance'),
        '--timerange', '20240101-20240201', '--cache', 'none', '--export', 'trades',
        '--export-directory', str(work / 'backtest_results'), '--log-file', str(work / 'native-backtest.log')]


def validate(work, history):
    work, history = Path(work).resolve(), Path(history).resolve()
    report = {'scope': 'NFI_RUNTIME_CONFIG_PREFLIGHT_V1', 'status': 'PREFLIGHT_FAILED',
        'network_requests': 0, 'orders': 0, 'backtest_started': False,
        'checked_at': datetime.now(UTC).isoformat(), 'stage': 'RUNTIME_IMPORT'}
    try:
        with disconnected():
            import freqtrade
            from freqtrade.commands.arguments import Arguments
            from freqtrade.configuration import Configuration
            from freqtrade.configuration.config_validation import validate_config_consistency
            from freqtrade.enums import RunMode
            from freqtrade.resolvers import StrategyResolver
            if freqtrade.__version__ != '2026.9':
                raise ValueError('PINNED_FREQTRADE_2026_9_REQUIRED')
            report['stage'] = 'CLI_CONFIGURATION'
            args = Arguments(cli_arguments(work, history)).get_parsed_arg()
            config = Configuration(args, RunMode.BACKTEST).get_config()
            report['stage'] = 'SOURCE_STRATEGY_RESOLUTION'
            StrategyResolver.load_strategy(config)
            report['stage'] = 'COMPLETE_SCHEMA_VALIDATION'
            validate_config_consistency(config)
            expected = (history / 'binance').resolve()
            if (Path(config['datadir']).resolve() != expected or not expected.is_dir()
                or not (expected / 'BTC_USDT-5m.feather').is_file()):
                raise ValueError('EXPLICIT_FROZEN_DATADIR_REQUIRED')
            if (config['telegram']['enabled'] or config['api_server']['enabled']
                or not config['dry_run'] or config['max_open_trades'] != 6
                or config['stake_amount'] != 'unlimited'):
                raise ValueError('SOURCE_RUN_SETTINGS_REQUIRED')
            report.update(status='RUNTIME_CONFIG_OK', stage='COMPLETE',
                config_sha256=sha256((work / 'config.json').read_bytes()).hexdigest(),
                strategy_sha256=sha256((work / 'strategies/NostalgiaForInfinityX8.py').read_bytes()).hexdigest(),
                freqtrade_version=freqtrade.__version__, resolved_datadir=str(expected),
                strategy=config['strategy'], max_open_trades=config['max_open_trades'],
                stake_amount=config['stake_amount'], configured_pairs=len(config['exchange']['pair_whitelist']))
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        raise
    finally:
        (work / 'runtime-preflight.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work', type=Path, default=Path('/work'))
    p.add_argument('--history', type=Path, default=Path('/history'))
    a = p.parse_args()
    # The provider's schema logger otherwise emits hundreds of lines on failure.
    logging.getLogger('freqtrade.configuration.config_validation').disabled = True
    try:
        report = validate(a.work, a.history)
    except Exception as exc:
        print('NFI_RUNTIME_PREFLIGHT=FAILED; ERROR_TYPE=' + type(exc).__name__ + '; SEE=runtime-preflight.json')
        raise SystemExit(2) from None
    print('NFI_RUNTIME_PREFLIGHT=' + report['status'] + '; DATADIR=' + report['resolved_datadir'])


if __name__ == '__main__':
    main()
