from hashlib import sha256
import json
import zipfile

import pytest

from app.strategy_lab import nfi_source_reproduction as m


def test_json_comments_do_not_destroy_urls_or_comment_like_strings():
    assert m.jsonc('{"url":"https://example.org/a/*x*/", // c\n"n":1/*c*/}') == {
        'url': 'https://example.org/a/*x*/', 'n': 1}


def test_source_preflight_refuses_changed_source_before_creating_output(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    (source / 'NostalgiaForInfinityX8.py').write_text('changed')
    with pytest.raises(ValueError, match='PINNED_SOURCE_MISMATCH'):
        m.prepare(source, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_history_missing_does_not_silently_reduce_universe(tmp_path):
    work = tmp_path / 'work'; work.mkdir()
    config = work / 'config.json'; config.write_text('{}')
    m.write(work / 'source-run-manifest.json', {'prepared_hashes': {
        'config.json': m.digest(config)}, 'pairs': ['BTC/USDT', 'ETH/USDT'], 'timeframes': ['5m', '1h']})
    history = tmp_path / 'history'; (history / 'binance').mkdir(parents=True)
    (history / 'binance/BTC_USDT-5m.feather').write_bytes(b'fixture')
    result = m.history_check(work, history)
    assert result['status'] == 'MISSING_FILES' and len(result['missing']) == 3
    config.write_text('{"stoploss":-0.02}')
    with pytest.raises(ValueError, match='CHANGED'):
        m.history_check(work, history)


def native(tmp_path, *, wrong=False, empty=False):
    folder = tmp_path / 'backtest_results'; folder.mkdir()
    trades = [] if empty else [{'profit_abs': 2}, {'profit_abs': -1}, {'profit_abs': 0}]
    result = {'trades': trades, 'wins': 0 if empty else 1, 'losses': 0 if empty else 1,
              'total_trades': len(trades), 'profit_total_abs': 99 if wrong else (0 if empty else 1)}
    with zipfile.ZipFile(folder / 'result.zip', 'w') as z:
        z.writestr('backtest-result.json', json.dumps({'strategy': {'NostalgiaForInfinityX8': result}}))


def test_native_trade_ledger_reconciles_without_claiming_publication(tmp_path):
    native(tmp_path)
    result = m.summarize(tmp_path)
    assert result['closed_trades'] == 3 and result['win_rate'] == 1/3
    assert result['draws'] == 1 and result['simulated_net_pnl'] == 1
    assert not result['publication_reproduced'] and result['strategy_promotion'] == 'NONE'


def test_accounting_disagreement_is_not_summarized_as_success(tmp_path):
    native(tmp_path, wrong=True)
    with pytest.raises(ValueError, match='PNL'):
        m.summarize(tmp_path)


def test_no_trades_is_not_a_winning_backtest(tmp_path):
    native(tmp_path, empty=True)
    r = m.summarize(tmp_path)
    assert r['win_rate'] is None and r['status'] == 'NO_TRADE_EVIDENCE'
