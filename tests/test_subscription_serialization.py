from threading import Event, Thread
import pytest

from tests.test_webull_stream_session_lifecycle import Client, backend


class ClientException(Exception):
    error_code='SDK_REJECTED'
    status_code=417


def test_concurrent_identical_replacements_do_not_overlap_or_unsubscribe_twice():
    entered=Event();release=Event();overlap=Event();second_started=Event();calls=[];errors=[]
    class SDK(Client):
        def unsubscribe(self,**kwargs):
            calls.append('unsubscribe')
            if entered.is_set() and not release.is_set():
                overlap.set();raise ClientException('concurrent unsubscribe')
            entered.set();assert release.wait(2)
    sdk=SDK('synthetic-session');source=backend(sdk);source.connect();source.subscribe(('AAA',))
    def replace(started=None):
        if started:started.set()
        try:source.subscribe(('BBB',))
        except Exception as error:errors.append(error)
    first=Thread(target=replace);second=Thread(target=replace,args=(second_started,))
    first.start();assert entered.wait(1)
    try:
        second.start();assert second_started.wait(1)
        assert not overlap.wait(.05)
    finally:
        release.set();first.join(2);second.join(2)
    assert not errors and not first.is_alive() and not second.is_alive()
    assert calls==['unsubscribe']
    assert source.subscription_state['active_count']==1 and source.subscription_acknowledged
    assert source._active_subscription==('BBB',)


def test_unsubscribe_failure_revokes_acknowledgement_and_keeps_retry_cleanup_required():
    class SDK(Client):
        def unsubscribe(self,**kwargs):raise ClientException('synthetic failure')
    source=backend(SDK('synthetic-session'));source.connect();source.subscribe(('AAA',))
    with pytest.raises(ClientException):source.subscribe(('BBB',))
    assert not source.subscription_acknowledged
    assert source._active_subscription==('AAA',)


def test_disconnect_during_subscription_cannot_be_marked_acknowledged():
    class SDK(Client):
        def subscribe(self,*args,**kwargs):
            super().subscribe(*args,**kwargs)
            if getattr(self,'drop',False):self.on_disconnect()
        def unsubscribe(self,**kwargs):pass
    sdk=SDK('synthetic-session');source=backend(sdk);source.connect();source.subscribe(('AAA',))
    sdk.drop=True
    with pytest.raises(RuntimeError,match='session.*changed|disconnected'):
        source.subscribe(('BBB',))
    assert not source.subscription_acknowledged


def test_failed_unsubscribe_saves_business_identity_without_raw_error_text(monkeypatch):
    from app.performance_diagnostics import PerformanceDiagnostics
    import app.webull.websocket_client as module
    diagnostics=PerformanceDiagnostics()
    monkeypatch.setattr(module,'performance_diagnostics',diagnostics)
    class SDK(Client):
        def unsubscribe(self,**kwargs):raise ClientException('secret=DO_NOT_RECORD')
    source=backend(SDK('synthetic-session'));source.connect();source.subscribe(('AAA',))
    with pytest.raises(ClientException):source.subscribe(('BBB',))
    events=diagnostics.stream_observability()['events']
    failure=next(e for e in events if e['event']=='UNSUBSCRIBE_FAILED')
    assert failure['exception_class']=='ClientException'
    assert failure['sdk_error_code']=='SDK_REJECTED' and failure['http_status']==417
    assert 'DO_NOT_RECORD' not in str(events)


@pytest.mark.parametrize('operation',['connect','disconnect'])
def test_lifecycle_operation_waits_for_subscription_transaction(operation):
    entered=Event();release=Event();started=Event();finished=Event();errors=[]
    class SDK(Client):
        def unsubscribe(self,**kwargs):entered.set();assert release.wait(2)
    source=backend(SDK('old'),factory=lambda:Client('new'))
    source.connect();source.subscribe(('AAA',))
    def replace():
        try:source.subscribe(('BBB',))
        except Exception as error:errors.append(error)
    def lifecycle():
        started.set()
        try:getattr(source,operation)()
        except Exception as error:errors.append(error)
        finally:finished.set()
    first=Thread(target=replace);second=Thread(target=lifecycle)
    first.start();assert entered.wait(1)
    try:
        second.start();assert started.wait(1)
        assert not finished.wait(.05)
    finally:release.set();first.join(2);second.join(2)
    assert finished.is_set() and not errors


def test_coordinator_coalesces_same_target_from_consumer_and_reference_worker():
    from app.live_scanner.coordinator import LiveScannerCoordinator
    from tests.test_live_scanner import FakeEngine,FakeTransport
    entered=Event();release=Event();started=Event();overlap=Event();errors=[]
    class Transport(FakeTransport):
        def subscribe(self,channels):
            if tuple(channels)==('BBB','CCC'):
                if entered.is_set() and not release.is_set():overlap.set()
                entered.set();assert release.wait(2)
            super().subscribe(channels)
    transport=Transport();coordinator=LiveScannerCoordinator(transport,FakeEngine())
    coordinator.start();before=len(transport.subscriptions)
    coordinator._scanner_channels=('BBB','CCC');coordinator._subscription_bootstrap_pending=False
    def sync(signal=None):
        if signal:signal.set()
        try:coordinator._sync_subscription()
        except Exception as error:errors.append(error)
    first=Thread(target=sync);second=Thread(target=sync,args=(started,))
    first.start();assert entered.wait(1)
    try:
        second.start();assert started.wait(1)
        assert not overlap.wait(.05)
    finally:
        release.set();first.join(2);second.join(2);coordinator.stop();coordinator.disconnect()
    assert not errors
    assert len(transport.subscriptions)==before+1 and coordinator.channels==('BBB','CCC')


def test_direct_subscription_consumer_failure_has_stage_and_sdk_code(monkeypatch):
    from app.performance_diagnostics import PerformanceDiagnostics
    from app.services.runtime_drivers.broker import DesktopBrokerRuntimeDriver
    import app.services.runtime_drivers.broker as module
    diagnostics=PerformanceDiagnostics();monkeypatch.setattr(module,'performance_diagnostics',diagnostics)
    class Scanner:
        last_failure_stage='SUBSCRIPTION_SYNC'
        def run_available(self):raise ClientException('secret=DO_NOT_RECORD')
    class Transport:
        def halt_callback_ingestion(self):pass
    driver=object.__new__(DesktopBrokerRuntimeDriver)
    driver._scanner=Scanner();driver._market_data_stop=Event()
    driver._market_data_consumer_attempted=False
    driver._market_data_transport=lambda:Transport()
    driver._publish_terminal_market_data_failure=lambda error:None
    stop=Event();driver._receive_market_data(stop)
    assert stop.is_set() and driver._market_data_consumer_state=='TERMINAL_FAILED'
    failure=next(e for e in diagnostics.stream_observability()['events'] if e['event']=='CONSUMER_FAILED')
    assert failure['failure_stage']=='SUBSCRIPTION_SYNC' and failure['sdk_error_code']=='SDK_REJECTED'
    assert 'DO_NOT_RECORD' not in str(diagnostics.stream_observability())
    assert diagnostics.stream_metrics()['terminal_stream_failures']==1
