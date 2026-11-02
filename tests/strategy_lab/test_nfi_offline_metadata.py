from hashlib import sha256
import json
import socket

import pytest

from app.strategy_lab import nfi_offline_metadata as m
from app.strategy_lab import nfi_source_reproduction as source


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    data = {'serverTime': 1765147603904, 'symbols': [
        {'baseAsset': 'T' + str(i), 'quoteAsset': 'USDT', 'status': 'TRADING',
         'filters': [{'filterType': t} for t in ('PRICE_FILTER', 'LOT_SIZE', 'NOTIONAL')]}
        for i in range(160)]}
    path = tmp_path / 'recording.json'
    def save():
        path.write_text(json.dumps(data))
        monkeypatch.setattr(m, 'SIZE', path.stat().st_size)
        monkeypatch.setattr(m, 'SHA256', sha256(path.read_bytes()).hexdigest())
    save()
    return path, data, save


def test_recording_remains_explicitly_ineligible_for_historical_execution_claims(recorded):
    path, _, _ = recorded
    _, context = m.load_metadata(path, ['T0/USDT'])
    assert context['source_class'] == 'THIRD_PARTY_POST_PERIOD_TEST_RECORDING'
    assert not context['historical_execution_rules_verified'] and not context['qualification_eligible']
    assert context['recorded_server_time'].startswith('2025-12')


def test_altered_or_incomplete_metadata_is_not_fabricated(recorded):
    path, data, save = recorded
    with pytest.raises(ValueError, match='ALL_CONFIGURED'):
        m.load_metadata(path, ['UNKNOWN/USDT'])
    data['symbols'][0]['filters'] = []
    save()
    with pytest.raises(ValueError, match='RULES_REQUIRED'):
        m.load_metadata(path, ['T0/USDT'])
    path.write_text(path.read_text().replace('TRADING', 'BREAK', 1))
    with pytest.raises(ValueError, match='PINNED_METADATA'):
        m.load_metadata(path, ['T0/USDT'])


def test_inactive_flags_are_preserved_and_reported(recorded):
    path, data, save = recorded
    data['symbols'][0]['status'] = 'BREAK'; save()
    got, context = m.load_metadata(path, ['T0/USDT'])
    assert got['symbols'][0]['status'] == 'BREAK'
    assert context['inactive_in_recording'] == ['T0/USDT']


def test_duplicate_market_refused(recorded):
    path, data, save = recorded
    data['symbols'].append(data['symbols'][0]); save()
    with pytest.raises(ValueError, match='DUPLICATE'):
        m.load_metadata(path, ['T0/USDT'])


def test_replay_network_guard_blocks_connections_and_restores_socket():
    original = socket.getaddrinfo
    with m.disconnected():
        with pytest.raises(RuntimeError, match='NETWORK_DISABLED'):
            socket.getaddrinfo('api.binance.com', 443)
    assert socket.getaddrinfo is original


def test_summary_cannot_lose_post_period_metadata_warning(tmp_path):
    import zipfile
    results = tmp_path / 'backtest_results'; results.mkdir()
    native = {'strategy': {'NostalgiaForInfinityX8': {'trades': [], 'wins': 0, 'losses': 0,
              'total_trades': 0, 'profit_total_abs': 0}}}
    with zipfile.ZipFile(results / 'result.zip', 'w') as z:
        z.writestr('backtest-result.json', json.dumps(native))
    source.write(tmp_path / 'metadata-context.json', {'qualification_eligible': False,
        'source_class': 'THIRD_PARTY_POST_PERIOD_TEST_RECORDING', 'limitations': ['Post-period rules']})
    summary = source.summarize(tmp_path)
    assert summary['scope'] == 'NFI_OFFLINE_POST_PERIOD_METADATA_RESEARCH_V1'
    assert not summary['qualification_eligible'] and not summary['publication_reproduced']
    assert 'Post-period rules' in summary['limitations']
