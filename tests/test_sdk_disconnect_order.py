"""Exercise real SDK/Paho shutdown with local sockets and no provider calls."""
import socket
from threading import Event
from types import SimpleNamespace as NS

import pytest
from app.live_scanner.transport import ReceiveTransportAdapter
from app.webull.sdk_streaming_adapter import (
    create_official_stream_backend, WebullStreamingCredentials, WebullMarketSubscription,
)
from app.webull.websocket_client import OfficialSdkStreamBackend, WebullWebSocketClient
from tests.test_webull_stream_session_lifecycle import Client


@pytest.mark.parametrize("request_stop", [True, False])
def test_real_sdk_disconnect_drains_before_join_without_retry_delay(monkeypatch, request_stop):
    from webull.core.http.initializer.client_initializer import ClientInitializer
    import webull.data.internal.quotes_client as quotes
    import paho.mqtt.client as mqtt
    monkeypatch.setattr(ClientInitializer, 'initializer', lambda *a: None)
    retries = []
    # A broken ordering must fail promptly rather than sleep for the SDK's 10s.
    def retry_sleep(seconds):
        if seconds:
            retries.append(seconds)
            raise RuntimeError('unexpected SDK retry during deliberate shutdown')
    monkeypatch.setattr(quotes, 'time', NS(sleep=retry_sleep))
    backend = create_official_stream_backend(
        WebullStreamingCredentials('synthetic', 'synthetic', 'synthetic'),
        WebullMarketSubscription('US_STOCK', ('QUOTE',)), tls_enable=False)
    sdk = backend.client
    # Use the actual SDK thread, actual MQTT loop/disconnect and local sockets.
    # Replace only initialization and network connection; no remote endpoint.
    local, peer = socket.socketpair()
    local.setblocking(False)
    def connect_local(*args):
        sdk._sock = local
        sdk._state = mqtt.mqtt_cs_connected
        sdk.on_connect_success(sdk, sdk.api_client, sdk.get_session_id())
    monkeypatch.setattr(sdk, '_quotes_connect', connect_local)
    client = WebullWebSocketClient(backend, lambda _: None,
        NS(maximum_attempts=0), lambda _: None, NS(log=lambda *a, **k: None))
    transport = ReceiveTransportAdapter(client)
    worker = None
    try:
        transport.connect()
        worker = sdk._thread
        assert worker is not None and worker.is_alive()
        if request_stop:
            transport.request_stop()
        transport.disconnect(timeout_seconds=2)
        assert not worker.is_alive()
        assert sdk._thread is None
        assert not retries
        assert not backend.heartbeat_ok and not backend.registration_ready
        assert not backend.subscription_acknowledged
        assert backend.connection_diagnostics['failure'] is None
    finally:
        backend.request_stop()
        sdk.disconnect()
        if worker is not None: worker.join(2)
        local.close(); peer.close()


def test_replacement_disconnects_old_worker_before_join():
    disconnected = Event()
    class OwnedClient(Client):
        def disconnect(self):
            disconnected.set(); super().disconnect()
            self.on_disconnect()
        def loop_stop(self):
            assert disconnected.is_set(), 'join would block before disconnect'
            super().loop_stop()
    old = OwnedClient('old'); fresh = Client('fresh')
    backend = OfficialSdkStreamBackend(old, sdk_client_factory=lambda: fresh,
        registration_grace_seconds=0, sleeper=lambda _: None)
    backend.connect()
    events = []
    backend.set_lifecycle_sink(lambda event, *args: events.append(event))
    backend.connect()
    assert 'unexpected_stream_termination' not in events
    assert old.calls == ['connect', 'disconnect', 'loop_stop']
    assert backend.client is fresh and backend.registration_ready
    backend.disconnect()


def test_disconnect_failure_still_stops_loop_and_is_not_reported_as_success():
    class BrokenClient(Client):
        def disconnect(self):
            self.calls.append('disconnect'); raise OSError('synthetic failure')
    sdk = BrokenClient('synthetic')
    backend = OfficialSdkStreamBackend(sdk, registration_grace_seconds=0,
        sleeper=lambda _: None)
    backend.connect()
    with pytest.raises(OSError): backend.disconnect()
    assert sdk.calls == ['connect', 'disconnect', 'loop_stop']
    assert not backend.registration_ready and not backend.heartbeat_ok


def test_real_sdk_retry_wait_is_cancelled_without_another_connection(monkeypatch):
    from webull.core.http.initializer.client_initializer import ClientInitializer
    monkeypatch.setattr(ClientInitializer, 'initializer', lambda *a: None)
    backend = create_official_stream_backend(
        WebullStreamingCredentials('synthetic', 'synthetic', 'synthetic'),
        WebullMarketSubscription('US_STOCK', ('QUOTE',)), tls_enable=False)
    sdk = backend.client
    waiting = Event(); attempts = []
    def fail(*args):
        attempts.append(1); raise OSError('synthetic network failure')
    def delay(context):
        waiting.set(); return 10000
    monkeypatch.setattr(sdk, '_quotes_connect', fail)
    monkeypatch.setattr(sdk._retry_policy.delegate, 'compute_delay_before_next_retry', delay)
    sdk.connect_and_loop_start()
    worker = sdk._thread
    try:
        assert waiting.wait(1)
        backend.request_stop()
        worker.join(1)
        assert not worker.is_alive()
        assert attempts == [1]
        sdk.loop_stop()
    finally:
        backend.request_stop(); worker.join(1)


def test_running_retry_policy_keeps_original_decision_and_delay(monkeypatch):
    from app.webull.sdk_streaming_adapter import _CancellableQuotesRetryPolicy
    calls = []; context = object()
    delegate = NS(should_retry=lambda c: calls.append(('decision', c)) or 2,
        compute_delay_before_next_retry=lambda c: calls.append(('delay', c)) or 1250)
    policy = _CancellableQuotesRetryPolicy(delegate, 1)
    waits = []
    monkeypatch.setattr(policy.stopped, 'wait', lambda seconds: waits.append(seconds))
    assert policy.should_retry(context) == 2
    assert policy.compute_delay_before_next_retry(context) == 0
    assert waits == [1.25] and calls == [('decision', context), ('delay', context)]
    policy.stopped.set()
    assert policy.should_retry(context) == 1
    assert len(calls) == 2
