"""Report-only pending Stop continues without restarting or bypassing execution."""
from threading import Event
from types import SimpleNamespace as NS
import pytest
from app.services.runtime_drivers.broker import DesktopBrokerRuntimeDriver
from app.strategies.warrior_momentum.report_worker import ReportWorkerShutdownPending
from app.services import RuntimeService
from app.operations_core import OperationsBus
from app.performance_diagnostics import PerformanceDiagnostics


def driver(stop):
    d = object.__new__(DesktopBrokerRuntimeDriver)
    d._market_event_observer = NS(stop=stop)
    d.health_updates = []
    d._publish_health = lambda *args, **kwargs:d.health_updates.append(args)
    return d


def test_pending_report_completes_on_same_operator_stop_without_runtime_failed():
    calls = []
    def stop():
        calls.append('close')
        if len(calls) < 3:
            raise ReportWorkerShutdownPending('report still owns capture')
    d = driver(stop)
    class RuntimeDriver:
        environment = 'PAPER'
        active_model = 'fixture'
        cycles_completed = 0
        def run(self, *, stop_event, cycle_sink):
            stop_event.wait(2)
            d._stop_observer()
    diagnostics = PerformanceDiagnostics()
    service = RuntimeService(OperationsBus(), RuntimeDriver, diagnostics=diagnostics)
    assert service.start()
    service.stop()
    assert service.wait(2)
    assert calls == ['close', 'close', 'close']
    assert len(d.health_updates) == 2
    assert all(x[2].runtime_status == 'STOPPING' for x in d.health_updates)
    assert diagnostics.shutdown_metrics()['shutdown_failure_present'] is False
    assert diagnostics.shutdown_metrics()['shutdown_cleanup_completed'] is True


def test_report_still_pending_after_three_attempts_remains_a_failure():
    calls = []
    error = ReportWorkerShutdownPending('report still owns capture')
    def stop():
        calls.append(1)
        raise error
    d = driver(stop)
    with pytest.raises(ReportWorkerShutdownPending) as caught:
        d._stop_observer()
    assert caught.value is error and len(calls) == 3


@pytest.mark.parametrize('error', [TimeoutError('Grok reconciliation pending'),
                                  RuntimeError('real cleanup failure')])
def test_unrelated_cleanup_error_is_not_retried_or_hidden(error):
    calls = []
    def stop():
        calls.append(1)
        raise error
    with pytest.raises(type(error)) as caught:
        driver(stop)._stop_observer()
    assert caught.value is error and calls == [1]
