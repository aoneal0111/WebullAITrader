"""Real thread handoff: Stop cancels connection waits, not provider failures."""
from threading import Event, Thread
from types import SimpleNamespace as NS
import pytest

from app.webull.errors import StreamShutdownRequested
from app.webull.websocket_client import OfficialSdkStreamBackend, WebullWebSocketClient
from app.live_scanner.transport import ReceiveTransportAdapter
from tests.test_webull_stream_session_lifecycle import Client
from tests.test_stale_feed_watchdog import _driver, _Scanner
from app.services.runtime_drivers.broker import DesktopBrokerRuntimeDriver
import app.services.runtime_drivers.broker as driver_module


def test_stop_reaches_backend_and_interrupts_long_connect_wait():
    entered = Event()
    class PendingClient(Client):
        def connect_and_loop_start(self, **kwargs):
            self.calls.append('connect'); entered.set()  # no acknowledgement
    sdk = PendingClient('synthetic')
    backend = OfficialSdkStreamBackend(sdk, connect_timeout_seconds=30,
        registration_grace_seconds=0, sleeper=lambda _:None)
    logger = NS(log=lambda *a, **k:None)
    client = WebullWebSocketClient(backend, lambda _:None,
        NS(maximum_attempts=1, backoff_seconds=0), lambda _:None, logger)
    transport = ReceiveTransportAdapter(client)
    errors = []
    def connect():
        try: transport.connect()
        except Exception as error: errors.append(error)
    thread = Thread(target=connect)
    thread.start()
    try:
        assert entered.wait(1)
        # request_stop must not wait on connect's session/adapter lock.
        assert transport.request_stop()
        thread.join(1)
        assert not thread.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], StreamShutdownRequested)
        sdk.on_connect_success(sdk, sdk.api_client, 'synthetic')
        assert not backend.heartbeat_ok
        with pytest.raises(StreamShutdownRequested): transport.connect()
    finally:
        backend.request_stop(); thread.join(1)


def test_watchdog_does_not_reconnect_after_stop(monkeypatch):
    scanner = _Scanner(); driver = _driver(scanner)
    driver._shutdown_requested = True
    monkeypatch.setattr(driver_module, 'monotonic', lambda:20)
    driver._run_feed_watchdog()
    assert scanner.recoveries == 0 and not driver._health


def test_watchdog_cancelled_by_stop_is_not_recovery_failure(monkeypatch):
    scanner = _Scanner(); driver = _driver(scanner)
    def recover():
        driver._shutdown_requested = True
        raise StreamShutdownRequested('local stop')
    scanner.recover_stream = recover
    monkeypatch.setattr(driver_module, 'monotonic', lambda:20)
    driver._run_feed_watchdog()
    assert not driver._feed_recovery_pending
    assert all(event != 'MARKET_DATA_RECOVERY_FAILED' for event, _ in driver._health)


def test_driver_cancels_transport_before_joining_receive_worker():
    calls = []
    transport = NS(request_stop=lambda:calls.append('cancel'),
                   halt_callback_ingestion=lambda:calls.append('halt'))
    class Worker:
        def join(self, timeout):
            assert calls == ['cancel', 'halt']; calls.append('join')
        def is_alive(self): return False
    driver = object.__new__(DesktopBrokerRuntimeDriver)
    driver._market_data = object(); driver._scanner = None
    driver._market_data_transport = lambda:transport
    driver._market_data_stop = Event(); driver._market_data_thread = Worker()
    driver._market_data_consumer_state = 'RUNNING'; driver._market_data_connected = False
    driver._stop_market_data()
    assert calls == ['cancel', 'halt', 'join']


def test_genuine_connect_failure_remains_failure_with_safe_provider_identity(monkeypatch):
    import app.webull.websocket_client as module
    from app.performance_diagnostics import PerformanceDiagnostics
    from app.webull.errors import NetworkError
    diagnostics = PerformanceDiagnostics()
    monkeypatch.setattr(module, 'performance_diagnostics', diagnostics)
    class ProviderFailure(Exception):
        error_code = 'INVALID_SESSION'
        status_code = 417
    def connect(): raise ProviderFailure('secret=NEVER_EXPORT')
    client = WebullWebSocketClient(NS(connect=connect), lambda _:None,
        NS(maximum_attempts=1), lambda _:None, NS(log=lambda *a, **k:None))
    with pytest.raises(NetworkError): client.connect()
    failure = next(e for e in diagnostics.stream_observability()['events']
                   if e['event'] == 'CONNECT_FAILED')
    assert failure['sdk_error_code'] == 'INVALID_SESSION'
    assert failure['http_status'] == 417
    assert 'NEVER_EXPORT' not in str(diagnostics.stream_observability())


def test_connect_identity_diagnostic_distinguishes_api_from_session_mismatch(monkeypatch):
    import app.webull.websocket_client as module
    from app.performance_diagnostics import PerformanceDiagnostics
    diagnostics = PerformanceDiagnostics()
    monkeypatch.setattr(module, 'performance_diagnostics', diagnostics)
    sdk = Client('PRIVATE_SESSION')
    backend = OfficialSdkStreamBackend(sdk)
    backend._on_connect_success(sdk, object(), 'PRIVATE_SESSION')
    event = diagnostics.stream_observability()['events'][-1]
    assert event['event'] == 'CONNECT_IDENTITY_MISMATCH'
    assert event['session_id_matches'] is True
    assert event['api_client_matches'] is False
    assert 'PRIVATE_SESSION' not in str(event)
