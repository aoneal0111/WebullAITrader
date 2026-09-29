from threading import Event, RLock

import pytest

from app.live_scanner.transport import ReceiveTransportAdapter
from app.live_scanner.authoritative_lane import AuthoritativeLaneFailure
from app.services.runtime_drivers.broker import DesktopBrokerRuntimeDriver
from app.webull.websocket_client import OfficialSdkStreamBackend


class _Sdk:
    def __init__(self):
        self._client_id = "session"
        self.api_client = object()
        self.on_quotes_message = None
        self.on_connect_success = None
        self.on_disconnect = None
        self.halts = 0

    def halt_callback_ingestion(self):
        self.halts += 1

    def disconnect(self):
        return None


def test_transport_forwards_halt_and_is_idempotent():
    sdk = _Sdk()
    adapter = ReceiveTransportAdapter(sdk)
    assert adapter.halt_callback_ingestion() is True
    assert adapter.halt_callback_ingestion() is True
    assert sdk.halts == 2


def test_transport_without_halt_capability_is_explicitly_unsupported():
    class Client:
        def disconnect(self):
            return None

    assert ReceiveTransportAdapter(Client()).halt_callback_ingestion() is False


def test_backend_halt_drains_queue_and_rejects_later_callbacks():
    sdk = _Sdk()
    backend = OfficialSdkStreamBackend(sdk, ingestion_capacity=4)
    for index in range(4):
        backend._on_quotes_message(sdk, "quotes", {"index": index})
    assert backend.memory_metrics()["messages_enqueued"] == 4

    backend.halt_callback_ingestion()
    before = backend.memory_metrics()
    for index in range(10):
        backend._on_quotes_message(sdk, "quotes", {"index": index})
    after = backend.memory_metrics()

    assert after["messages_enqueued"] == before["messages_enqueued"]
    assert after["current_depth"] == 0
    assert backend.ingestion_metrics()["callbacks_rejected_after_halt"] == 10


def test_consumer_exception_records_stage_and_terminal_state():
    class Scanner:
        last_failure_stage = "CANONICAL_REDUCTION"

        def run_available(self):
            raise ValueError("not serialized")

    class Transport:
        def __init__(self):
            self.halted = 0

        def halt_callback_ingestion(self):
            self.halted += 1

    scanner = Scanner()
    transport = Transport()
    driver = object.__new__(DesktopBrokerRuntimeDriver)
    driver._scanner = scanner
    driver._market_data_stop = Event()
    driver._market_data_consumer_state = "RUNNING"
    driver._market_data_consumer_attempted = False
    driver._market_data_first_event_consumed = False
    driver._market_data_failure_stage = None
    driver._market_data_failure_exception_class = None
    driver._market_data_transport = lambda: transport
    failures = []
    driver._publish_terminal_market_data_failure = failures.append

    driver._receive_market_data(Event())

    assert transport.halted == 1
    assert driver._market_data_consumer_state == "TERMINAL_FAILED"
    assert driver._market_data_failure_stage == "CANONICAL_REDUCTION"
    assert driver._market_data_failure_exception_class == "ValueError"
    assert len(failures) == 1


def test_authoritative_lane_failure_is_not_classified_as_stream_failure():
    driver = object.__new__(DesktopBrokerRuntimeDriver)
    driver._event_lock = RLock()
    driver._terminal_stream_failure_published = False
    published = []
    driver._publish_health = lambda event_type, message, health: published.append(
        (event_type, message, health)
    )
    error = AuthoritativeLaneFailure("authoritative lane worker failed")
    error.__cause__ = ValueError("paper invariant")

    driver._publish_terminal_market_data_failure(error)

    assert published[0][0] == "AUTHORITATIVE_PROCESSING_FAILURE"
    assert "Webull market-data streaming failed" not in published[0][1]
    assert published[0][2].streaming_status == "CONNECTED"
    assert published[0][2].execution_status == "BLOCKED - AUTHORITATIVE FAILURE"


def test_transport_failure_remains_market_data_terminal_failure():
    driver = object.__new__(DesktopBrokerRuntimeDriver)
    driver._event_lock = RLock()
    driver._terminal_stream_failure_published = False
    published = []
    driver._publish_health = lambda event_type, message, health: published.append(
        (event_type, message, health)
    )

    driver._publish_terminal_market_data_failure(RuntimeError("socket closed"))

    assert published[0][0] == "MARKET_DATA_TERMINAL_FAILURE"
    assert "Webull market-data streaming failed" in published[0][1]
    assert published[0][2].market_data_status == "REST_ONLY"


def test_transport_disconnect_is_bounded():
    class Hanging:
        def disconnect(self):
            Event().wait(30)

    with pytest.raises(TimeoutError):
        ReceiveTransportAdapter(Hanging()).disconnect(timeout_seconds=0.01)
