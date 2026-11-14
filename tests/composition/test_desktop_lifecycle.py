from types import SimpleNamespace
import pytest

from app.composition.desktop_lifecycle import close_desktop_composition
from app.strategies.warrior_momentum.report_worker import ReportWorkerShutdownPending


def test_execution_worker_stops_before_durable_paper_store_closes():
    store = {"open": True}
    def stop_worker():
        # Any completion work during drain must retain its persistence boundary.
        assert store["open"]
        store["worker_stopped"] = True
    def close_store():
        assert store.get("worker_stopped")
        store["open"] = False
    composition = SimpleNamespace(
        runtime_service=SimpleNamespace(close=lambda **kwargs: True),
        warrior_forward_sidecar=SimpleNamespace(stop=stop_worker),
        paper_trading_commands=SimpleNamespace(close=close_store),
        state_store=SimpleNamespace(close=lambda: None),
    )
    assert close_desktop_composition(composition)
    assert not store["open"]


def test_pending_report_does_not_skip_independent_capture_cleanup(monkeypatch):
    calls = []
    def stop_worker():
        calls.append('warrior')
        raise ReportWorkerShutdownPending('report still running')
    composition = SimpleNamespace(
        runtime_service=SimpleNamespace(close=lambda **kwargs: True),
        warrior_forward_sidecar=SimpleNamespace(stop=stop_worker),
        paper_trading_commands=SimpleNamespace(close=lambda: calls.append('paper')),
        daily_research=SimpleNamespace(close=lambda: calls.append('capture')),
        state_store=SimpleNamespace(close=lambda: calls.append('state')),
    )
    monkeypatch.setattr('app.composition.desktop_lifecycle.performance_diagnostics.finish_run',
                        lambda: calls.append('checkpoint'))
    assert not close_desktop_composition(composition)
    assert calls == ['warrior', 'paper', 'capture', 'state', 'checkpoint']


def test_execution_shutdown_timeout_does_not_close_its_durable_store():
    calls = []
    def stop_worker():
        raise TimeoutError('execution acknowledgement still pending')
    composition = SimpleNamespace(
        runtime_service=SimpleNamespace(close=lambda **kwargs: True),
        warrior_forward_sidecar=SimpleNamespace(stop=stop_worker),
        paper_trading_commands=SimpleNamespace(close=lambda: calls.append('paper')),
        state_store=SimpleNamespace(close=lambda: calls.append('state')),
    )
    with pytest.raises(TimeoutError, match='acknowledgement'):
        close_desktop_composition(composition)
    assert not calls
