from threading import Event
from types import SimpleNamespace as NS
import pytest

from app.configuration import TradingEnvironment
from app.services.runtime_drivers.broker import DesktopBrokerRuntimeDriver
from app.webull.errors import NetworkError, AuthenticationError
from app.live_scanner.transport import ReceiveTransportAdapter
from app.live_scanner.coordinator import LiveScannerCoordinator
from tests.test_live_scanner import FakeEngine, FakeTransport, FakeEvent
from tests.test_webull_stream_session_lifecycle import Client, backend


class Stop(Event):
    def __init__(self):
        super().__init__();self.delays=[]
    def wait(self,timeout=None):
        self.delays.append(timeout)
        return self.is_set()


def driver(poll,environment=TradingEnvironment.PAPER):
    d=object.__new__(DesktopBrokerRuntimeDriver)
    d._configuration=NS(environment=environment)
    d._account_poller=poll;d._broker=object();d._clock=lambda:None
    d._health=[];d._publish_health=lambda *args:d._health.append(args)
    return d


def test_transient_paper_account_read_recovers_without_fake_snapshot_or_mutation():
    calls=[];snapshot=object()
    def poll(*args,**kwargs):
        calls.append(1)
        if len(calls)<3:raise NetworkError('synthetic transient read',retryable=True)
        return snapshot
    d=driver(poll);stop=Stop()
    assert d._poll_account_with_retry(stop) is snapshot
    assert len(calls)==3 and stop.delays==[1,2]
    assert len(d._health)==2
    assert all(h[2].account_status=='STALE' for h in d._health)


@pytest.mark.parametrize('error',[
    AuthenticationError('denied'), NetworkError('non-retryable'), ValueError('invalid')])
def test_nontransient_account_failure_is_not_retried(error):
    calls=[]
    def poll(*args,**kwargs):calls.append(1);raise error
    with pytest.raises(type(error)):driver(poll)._poll_account_with_retry(Stop())
    assert len(calls)==1


def test_repeated_network_failure_is_bounded_and_operator_stop_interrupts_retry():
    calls=[]
    def poll(*args,**kwargs):calls.append(1);raise NetworkError('synthetic',retryable=True)
    with pytest.raises(NetworkError):driver(poll)._poll_account_with_retry(Stop())
    assert len(calls)==3
    stop=Stop();stop.set()
    assert driver(poll)._poll_account_with_retry(stop) is None
    assert len(calls)==3


def test_live_account_path_keeps_existing_no_retry_behavior():
    calls=[]
    def poll(*args,**kwargs):calls.append(1);raise NetworkError('synthetic',retryable=True)
    with pytest.raises(NetworkError):driver(poll,TradingEnvironment.PRODUCTION)._poll_account_with_retry(Stop())
    assert len(calls)==1


def test_failed_reconnect_cleans_half_open_transport_and_preserves_authoritative_lane():
    class Transport(FakeTransport):
        def connect(self):
            super().connect()
            if self.connect_calls==2:raise NetworkError('connect timeout',retryable=True)
    transport=Transport();coordinator=LiveScannerCoordinator(transport,FakeEngine(),
        default_channels=('quotes',),asynchronous_authoritative=True,event_observer=lambda event:None)
    coordinator.start();lane=coordinator._authoritative_lane
    try:
        with pytest.raises(NetworkError):coordinator.recover_stream()
        assert transport.disconnect_calls==2
        assert not coordinator.connected and not coordinator.running
        assert coordinator._authoritative_lane is lane and lane.running
        coordinator.recover_stream()
        assert coordinator.connected and coordinator.running
        assert coordinator._authoritative_lane is lane
    finally:coordinator.stop();coordinator.disconnect()


def test_pending_disconnect_does_not_overlap_new_connection_or_second_disconnect():
    entered=Event();release=Event();calls=[]
    class Client:
        def disconnect(self):
            calls.append('disconnect');entered.set();release.wait(2)
        def connect(self):calls.append('connect')
    transport=ReceiveTransportAdapter(Client())
    try:
        with pytest.raises(TimeoutError):transport.disconnect(timeout_seconds=.01)
        assert entered.is_set()
        with pytest.raises(TimeoutError,match='pending'):transport.connect()
        with pytest.raises(TimeoutError,match='pending'):transport.disconnect(timeout_seconds=.01)
        assert calls==['disconnect']
    finally:release.set();transport._disconnect_thread.join(1)
    transport.connect();assert calls==['disconnect','connect']


def test_retired_sdk_callbacks_cannot_invalidate_new_connected_session():
    old=Client('old');new=Client('new');source=backend(old,factory=lambda:new)
    source.connect();old_disconnect=old.on_disconnect
    source.disconnect();source.connect()
    assert source.heartbeat_ok
    old_disconnect()
    old.on_connect_success(old,old.api_client,'old')
    assert source.heartbeat_ok and not source._identity_mismatch.is_set()
    new.on_disconnect()
    assert not source.heartbeat_ok


def test_operator_stop_during_account_backoff_does_not_retry():
    calls=[]
    def poll(*args,**kwargs):calls.append(1);raise NetworkError('synthetic',retryable=True)
    class StopOnWait(Stop):
        def wait(self,timeout=None):self.set();return True
    assert driver(poll)._poll_account_with_retry(StopOnWait()) is None
    assert len(calls)==1
