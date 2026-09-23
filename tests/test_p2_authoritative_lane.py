from __future__ import annotations

from datetime import UTC, datetime
from threading import Event
from time import monotonic, sleep

from app.live_scanner.authoritative_lane import (
    AuthoritativeEventLane,
    AuthoritativeLaneOverflow,
)
from app.live_scanner.coordinator import LiveScannerCoordinator
from app.webull.websocket_client import OfficialSdkStreamBackend


class _Transport:
    def __init__(self, events):
        self.events = list(events)
        self.subscriptions = []

    def connect(self):
        return None

    def disconnect(self):
        return None

    def subscribe(self, channels):
        self.subscriptions.append(tuple(channels))

    def read_event(self):
        return self.events.pop(0) if self.events else None

    def read_event_nowait(self):
        return self.read_event()

    def memory_metrics(self):
        return {"current_depth": len(self.events), "high_water_depth": len(self.events)}


class _Engine:
    active_symbols = ()
    subscription_symbols = ()

    def consume(self, event):
        return None

    def drain_evaluations(self, *, maximum=None):
        return ()

    def close(self):
        return None


class _TimedEvent:
    def __init__(self, sequence: int):
        self.sequence = sequence
        self.received_timestamp = datetime.now(UTC)


def test_authoritative_lane_preserves_order_and_drains_on_shutdown():
    received = []
    lane = AuthoritativeEventLane(received.append, capacity=4)
    lane.start()
    for value in range(4):
        lane.publish(value)
    lane.stop()
    assert received == [0, 1, 2, 3]
    metrics = lane.metrics()
    assert metrics["authoritative_events_enqueued"] == 4
    assert metrics["authoritative_events_dequeued"] == 4
    assert metrics["authoritative_lane_depth"] == 0


def test_authoritative_lane_overflow_is_visible_and_never_drops_silently():
    release = Event()
    started = Event()
    received = []

    def observer(value):
        if not received:
            started.set()
            release.wait(1.0)
        received.append(value)

    lane = AuthoritativeEventLane(observer, capacity=2)
    lane.start()
    lane.publish(1)
    assert started.wait(1.0)
    lane.publish(2)
    try:
        lane.publish(3)
    except AuthoritativeLaneOverflow:
        pass
    else:
        raise AssertionError("authoritative overflow was silently accepted")
    assert lane.metrics()["authoritative_lane_overflow"] == 1
    release.set()
    lane.stop()
    assert received == [1, 2]


def test_repeated_stop_after_worker_exit_does_not_block():
    lane = AuthoritativeEventLane(lambda event: None, capacity=2)
    lane.start()
    lane.stop()
    lane.stop()
    assert lane.metrics()["authoritative_lane_depth"] == 0


def test_slow_authoritative_observer_does_not_block_raw_ingestion_loop():
    events = list(range(3000))
    transport = _Transport(events)
    release = Event()
    observed = []

    def observer(event):
        release.wait(1.0)
        observed.append(event)

    coordinator = LiveScannerCoordinator(
        transport,
        _Engine(),
        asynchronous_authoritative=True,
        authoritative_lane_capacity=4096,
        maximum_events_per_cycle=3000,
        event_observer=observer,
    )
    coordinator.connect()
    coordinator.subscribe(("WHLR",))
    coordinator._running = True
    cycle = coordinator.run_available(maximum_events=3000)
    assert cycle.events_read == 3000
    assert coordinator.memory_metrics()["authoritative_lane_depth"] >= 2999
    release.set()
    coordinator.stop()
    assert observed == list(range(3000))


def test_raw_callback_fifo_has_explicit_fail_stop_capacity():
    client = type("Client", (), {})()
    client._client_id = "offline"
    client._quotes_session_id = "offline"
    client.api_client = object()
    client.on_quotes_message = None
    client.on_connect_success = None
    client.on_disconnect = None
    stream = OfficialSdkStreamBackend(client, ingestion_capacity=1)
    client.on_quotes_message(client, "offline", object())
    client.on_quotes_message(client, "offline", object())
    metrics = stream.ingestion_metrics()
    assert metrics["ingestion_queue_capacity"] == 1
    assert metrics["ingestion_queue_high_water"] == 1
    assert metrics["ingestion_queue_overflow"] == 1


def test_acceptance_profile_a_reports_end_to_end_warrior_latency():
    events = [_TimedEvent(index) for index in range(3000)]
    transport = _Transport(events)
    latencies = []

    def observer(event):
        latencies.append(
            (datetime.now(UTC) - event.received_timestamp).total_seconds() * 1000
        )

    coordinator = LiveScannerCoordinator(
        transport,
        _Engine(),
        asynchronous_authoritative=True,
        authoritative_lane_capacity=4096,
        maximum_events_per_cycle=3000,
        event_observer=observer,
    )
    coordinator.connect()
    coordinator.subscribe(("WHLR",))
    coordinator._running = True
    assert coordinator.run_available(maximum_events=3000).events_read == 3000
    coordinator.stop()
    ordered = sorted(latencies)
    assert len(ordered) == 3000
    p99 = ordered[int(len(ordered) * 0.99) - 1]
    print(
        "PROFILE_A callback_to_warrior_ms "
        f"p50={ordered[1499]:.3f} p90={ordered[2699]:.3f} "
        f"p95={ordered[2849]:.3f} p99={p99:.3f} max={ordered[-1]:.3f}"
    )
    assert p99 < 5000.0


def test_acceptance_profile_b_five_thousand_burst_is_visible_overflow():
    release = Event()
    started = Event()
    observed = []

    def observer(event):
        if not observed:
            started.set()
            release.wait(1.0)
        observed.append(event)

    coordinator = LiveScannerCoordinator(
        _Transport([_TimedEvent(index) for index in range(5000)]),
        _Engine(),
        asynchronous_authoritative=True,
        authoritative_lane_capacity=4096,
        maximum_events_per_cycle=5000,
        event_observer=observer,
    )
    coordinator.connect()
    coordinator.subscribe(("WHLR",))
    coordinator._running = True
    try:
        coordinator.run_available(maximum_events=5000)
    except RuntimeError:
        pass
    else:
        raise AssertionError("5,000 instant events unexpectedly fit the lane")
    metrics = coordinator.memory_metrics()
    print(
        "PROFILE_B lane depth/high_water/overflow "
        f"{metrics['authoritative_lane_depth']}/"
        f"{metrics['authoritative_lane_high_water']}/"
        f"{metrics['authoritative_lane_overflow']}"
    )
    assert metrics["authoritative_lane_overflow"] >= 1
    release.set()
    coordinator.stop()


def test_acceptance_profile_c_sustained_rate_drains_without_monotonic_growth():
    received = []

    def observer(event):
        sleep(0.0001)
        received.append(event)

    lane = AuthoritativeEventLane(observer, capacity=4096)
    lane.start()
    started = monotonic()
    for index in range(5000):
        lane.publish(index)
        sleep(0.0002)
    enqueue_seconds = monotonic() - started
    lane.stop()
    metrics = lane.metrics()
    print(
        "PROFILE_C enqueue_seconds/high_water/overflow "
        f"{enqueue_seconds:.3f}/{metrics['authoritative_lane_high_water']}/"
        f"{metrics['authoritative_lane_overflow']}"
    )
    assert len(received) == 5000
    assert metrics["authoritative_lane_overflow"] == 0
    assert metrics["authoritative_lane_depth"] == 0
