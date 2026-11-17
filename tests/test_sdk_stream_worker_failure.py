"""Surface fatal official-SDK worker errors instead of a false callback timeout."""
from threading import Event, Thread
from types import SimpleNamespace as NS
import pytest
from app.webull.websocket_client import OfficialSdkStreamBackend, WebullWebSocketClient
from app.webull.sdk_streaming_adapter import (create_official_stream_backend,
    WebullStreamingCredentials, WebullMarketSubscription)
from app.webull.errors import NetworkError, StreamShutdownRequested
from app.performance_diagnostics import PerformanceDiagnostics
from tests.test_webull_stream_session_lifecycle import Client


class SDKFault(Exception):
    error_code = 'SDK_HTTP_ERROR'
    status_code = 401


def test_real_sdk_initializer_failure_is_reported_without_callback_timeout(monkeypatch):
    import app.webull.websocket_client as module
    from webull.core.http.initializer.client_initializer import ClientInitializer
    diagnostics = PerformanceDiagnostics()
    monkeypatch.setattr(module, 'performance_diagnostics', diagnostics)
    calls = []
    def fail(*args, **kwargs):
        calls.append('initializer')
        raise SDKFault('app_secret=NEVER_EXPORT')
    monkeypatch.setattr(ClientInitializer, 'initializer', fail)
    backend = create_official_stream_backend(
        WebullStreamingCredentials('synthetic', 'synthetic', 'synthetic-session'),
        WebullMarketSubscription('US_STOCK', ('QUOTE',)), tls_enable=False)
    client = WebullWebSocketClient(backend, lambda _:None,
        NS(maximum_attempts=0), lambda _:None, NS(log=lambda *a, **k:None))
    try:
        with pytest.raises(NetworkError) as caught:
            client.connect()
        assert isinstance(caught.value.__cause__, SDKFault)
        assert calls == ['initializer']
        detail = backend.connection_diagnostics
        assert detail['phase'] == 'SDK_INITIALIZATION'
        assert detail['failure']['http_status'] == 401
        assert not backend.heartbeat_ok and not backend.registration_ready
        events = diagnostics.stream_observability()['events']
        failure = next(x for x in events if x['event'] == 'SDK_LOOP_FAILED')
        assert failure['sdk_error_code'] == 'SDK_HTTP_ERROR'
        assert failure['failure_stage'] == 'SDK_INITIALIZATION'
        assert 'NEVER_EXPORT' not in str(events) + str(detail)
    finally:
        backend.request_stop()
        backend.client.loop_stop()


class AsyncClient(Client):
    def __init__(self, session, fail_at='init'):
        super().__init__(session)
        self.fail_at = fail_at
        self.entered = Event(); self.release = Event()
    def _init_client(self):
        if self.fail_at == 'init': raise SDKFault('private')
    def _quotes_connect(self):
        if self.fail_at == 'socket': raise OSError('private host failure')
        self.on_connect_success(self, self.api_client, self.quotes_session_id)
    def connect_and_loop_forever(self):
        self._init_client(); self._quotes_connect(); self.entered.set()
        self.release.wait(2)
        if self.fail_at == 'active': raise SDKFault('private')
    def connect_and_loop_start(self, **kwargs):
        self._thread = Thread(target=self.connect_and_loop_forever)
        self._thread.start()
    def loop_stop(self):
        self.release.set()
        if getattr(self, '_thread', None): self._thread.join(2)


@pytest.mark.parametrize('phase,error_type', [('init', SDKFault), ('socket', OSError)])
def test_sdk_thread_failure_retains_exact_startup_stage(phase, error_type):
    sdk = AsyncClient('synthetic', phase)
    backend = OfficialSdkStreamBackend(sdk, connect_timeout_seconds=30,
        registration_grace_seconds=0, sleeper=lambda _:None)
    try:
        with pytest.raises(error_type): backend.connect()
        assert backend.connection_diagnostics['phase'] == (
            'SDK_INITIALIZATION' if phase == 'init' else 'SDK_SOCKET_CONNECT')
    finally:
        backend.request_stop(); sdk.loop_stop()


def test_post_connect_failure_revokes_readiness_and_is_seen_by_receive():
    sdk = AsyncClient('synthetic', 'active')
    backend = OfficialSdkStreamBackend(sdk, registration_grace_seconds=0,
        receive_timeout_seconds=0, sleeper=lambda _:None)
    backend.connect(); assert sdk.entered.wait(1)
    sdk.release.set(); sdk._thread.join(1)
    assert not backend.heartbeat_ok and not backend.subscription_acknowledged
    with pytest.raises(SDKFault): backend.receive()
    # A late success cannot revive a failed SDK worker.
    sdk.on_connect_success(sdk, sdk.api_client, sdk.quotes_session_id)
    assert not backend.heartbeat_ok
    backend.request_stop()
    with pytest.raises(StreamShutdownRequested): backend.receive()


def test_retired_sdk_failure_cannot_poison_current_generation():
    old = AsyncClient('old', 'active'); new = Client('new')
    backend = OfficialSdkStreamBackend(old, sdk_client_factory=lambda:new,
        registration_grace_seconds=0, sleeper=lambda _:None)
    backend.connect(); assert old.entered.wait(1)
    generation = backend._generation
    backend.connect()
    backend._record_sdk_loop_failure(old, generation, SDKFault('late retired error'))
    assert backend.heartbeat_ok
    assert backend.connection_diagnostics['failure'] is None
    backend.disconnect()


def test_sdk_internal_retry_keeps_safe_socket_failure_visible_before_deadline():
    class RetryingClient(AsyncClient):
        def connect_and_loop_forever(self):
            self._init_client()
            try:
                self._quotes_connect()
            except OSError:
                # Emulate the SDK retaining its own retry loop: no fatal return.
                self.entered.set(); self.release.wait(2)
    sdk = RetryingClient('synthetic', 'socket')
    backend = OfficialSdkStreamBackend(sdk, connect_timeout_seconds=.1,
        registration_grace_seconds=0, sleeper=lambda _:None)
    try:
        with pytest.raises(TimeoutError): backend.connect()
        info = backend.connection_diagnostics
        assert info['worker_alive'] is True
        assert info['failure'] is None
        assert info['last_stage_failure']['exception_class'] == 'OSError'
        assert info['last_stage_failure']['failure_stage'] == 'SDK_SOCKET_CONNECT'
        assert 'private host' not in str(info)
    finally:
        backend.request_stop(); sdk.loop_stop()
