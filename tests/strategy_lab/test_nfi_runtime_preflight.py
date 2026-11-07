from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.strategy_lab import nfi_source_reproduction as source
from app.strategy_lab import nfi_runtime_preflight as preflight
from app.strategy_lab import nfi_native_backtest as native


def test_disabled_sections_retain_all_schema_fields_and_strategy_rules(tmp_path):
    original = {'exchange': {'name': 'binance', 'pair_whitelist': ['P' + str(i) + '/USDT' for i in range(40)]},
        'max_open_trades': 6, 'stake_amount': 'unlimited', 'dry_run_wallet': 10000,
        'grinding_enable': True, 'stoploss': -.99,
        'api_server': {'enabled': True, 'listen_ip_address': '0.0.0.0', 'listen_port': 8080,
                       'username': '', 'password': '', 'jwt_secret_key': 'upstream-public-default'},
        'telegram': {'enabled': False, 'token': '', 'chat_id': '', 'notification_settings': {'status': 'silent'}}}
    for i, name in enumerate(source.CONFIGS):
        path = tmp_path / name; path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(json.dumps(original if i == 0 else {}))
    c = source.isolated_config(tmp_path)
    expected = deepcopy(original)
    expected['api_server']['enabled'] = False
    expected.update(dry_run=True, force_entry_enable=False, user_data_dir='/work',
                    strategy_path='/work/strategies', datadir='/history/binance')
    assert c == expected
    assert c['telegram']['token'] == c['telegram']['chat_id'] == ''


def test_shared_cli_passes_explicit_datadir_and_only_backtesting(tmp_path):
    args = preflight.cli_arguments(tmp_path / 'work', tmp_path / 'history')
    assert args[0] == 'backtesting'
    assert args[args.index('--datadir') + 1] == str(tmp_path / 'history/binance')
    assert args[args.index('--timerange') + 1] == '20240101-20240201'
    assert not {'trade', '--fee', '--stoploss'} & set(args)


@pytest.fixture
def work(tmp_path):
    work = tmp_path / 'work'; work.mkdir()
    history = tmp_path / 'history'; (history / 'binance').mkdir(parents=True)
    (work / 'strategies').mkdir()
    (work / 'strategies/NostalgiaForInfinityX8.py').write_text('# fixture\n')
    (work / 'config.json').write_text('{}')
    (work / 'backtest_results').mkdir()
    report = {'status': 'RUNTIME_CONFIG_OK', 'resolved_datadir': str(history / 'binance'),
        'config_sha256': sha256((work / 'config.json').read_bytes()).hexdigest(),
        'strategy_sha256': sha256((work / 'strategies/NostalgiaForInfinityX8.py').read_bytes()).hexdigest()}
    (work / 'runtime-preflight.json').write_text(json.dumps(report))
    return work, history


def test_zero_exit_without_archive_is_not_success(work):
    work, history = work
    r = native.run(work, history, execute=lambda *a, **k: SimpleNamespace(returncode=0))
    assert r['status'] == 'MISSING_OR_AMBIGUOUS_NATIVE_RESULT'


def test_nonzero_exit_with_archive_is_not_success(work):
    work, history = work
    def execute(*a, **k):
        (work / 'backtest_results/partial.zip').write_bytes(b'partial')
        return SimpleNamespace(returncode=2)
    assert native.run(work, history, execute=execute)['status'] == 'NATIVE_PROCESS_FAILED'


def test_changed_config_cannot_reuse_preflight(work):
    work, history = work
    (work / 'config.json').write_text('{"stoploss":-0.02}')
    with pytest.raises(ValueError, match='MATCHING_RUNTIME'):
        native.run(work, history, execute=lambda *a, **k: pytest.fail('Must not run'))


def test_one_archive_with_success_is_ready_for_separate_reconciliation(work):
    work, history = work
    def execute(args, **kwargs):
        assert args[1:4] == ['-m', 'freqtrade', 'backtesting']
        (work / 'backtest_results/result.zip').write_bytes(b'fixture; reconciliation validates content later')
        return SimpleNamespace(returncode=0)
    assert native.run(work, history, execute=execute)['status'] == 'NATIVE_RESULT_READY'


def test_repair_preserves_trading_fields_and_refuses_unrelated_changes(work, monkeypatch, tmp_path):
    work, _ = work
    old = {'max_open_trades': 6, 'telegram': {'enabled': False}, 'api_server': {'enabled': False}}
    new = {**old, 'telegram': {'enabled': False, 'token': '', 'chat_id': ''}}
    (work / 'config.json').write_text(json.dumps(old))
    source.write(work / 'source-run-manifest.json', {'scope': 'UNMODIFIED_NFI_SOURCE_BACKTEST_V1',
        'source_commit': source.COMMIT, 'prepared_hashes': {'config.json': source.digest(work / 'config.json')}})
    monkeypatch.setattr(source, 'HASHES', {'NostalgiaForInfinityX8.py': source.digest(work / 'strategies/NostalgiaForInfinityX8.py')})
    monkeypatch.setattr(source, 'isolated_config', lambda _: new)
    monkeypatch.setattr(source, 'prepare', lambda *a: {'prepared': True})
    assert source.repair_prepare(tmp_path, work, tmp_path / 'repaired')['prepared']
    new['max_open_trades'] = 2
    with pytest.raises(ValueError, match='PRESERVE_TRADING'):
        source.repair_prepare(tmp_path, work, tmp_path / 'refused')
