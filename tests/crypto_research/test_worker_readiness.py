"""Exercise real spawn IPC and temporary durable stores, with networking forbidden."""
import socket

from app.crypto_research.worker_readiness import check


def test_disconnected_round_trip_and_protection(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Network access forbidden in disconnected check')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    report = check()
    assert report['ready'] is True
    assert report['network_requests'] == report['broker_orders'] == report['grok_requests'] == 0
    assert report['installed_databases_opened'] == 0
    assert report['baseline_modified'] is False
    assert {case['exit_case'] for case in report['cases']} == {'TARGET', 'STOP'}
    for case in report['cases']:
        assert case['passed'] and all(case['checks'].values())
        assert case['simulated_fills'] == 2
        assert case['positions_remaining'] == 0
