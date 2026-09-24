from __future__ import annotations

from queue import Queue
from threading import Event
from time import monotonic, sleep

from app.live_scanner.authoritative_lane import AuthoritativeEventLane
from app.performance_diagnostics import PerformanceDiagnostics
from app.services.runtime_drivers.broker import DesktopBrokerRuntimeDriver
from app.strategies.warrior_momentum.desktop_sidecar import CompositeMarketEventObserver


def test_authoritative_stage_is_bounded_and_exception_safe() -> None:
    diagnostics = PerformanceDiagnostics()
    with diagnostics.authoritative_stage("authoritative_worker_event", symbol="TEST", event_type="QUOTE"):
        pass
    snapshot = diagnostics.snapshot()
    assert snapshot.authoritative_stage["stage"] is None
    assert snapshot.component_timings["authoritative.authoritative_worker_event"]["calls"] == 1
    assert snapshot.component_timings["authoritative_worker_event_complete"]["calls"] == 1


def test_slow_projection_sink_does_not_block_emit() -> None:
    received: list[object] = []
    driver = object.__new__(DesktopBrokerRuntimeDriver)
    driver._event_sink = lambda event: (sleep(0.15), received.append(event))
    driver._projection_queue = Queue(maxsize=8)
    driver._projection_stop = Event()
    driver._projection_thread = None
    driver._start_projection_worker()
    driver._projection_async_enabled = True
    try:
        started = monotonic()
        driver._emit(object())
        elapsed = monotonic() - started
        assert elapsed < 0.05
        driver._projection_queue.join()
        assert len(received) == 1
    finally:
        driver._stop_projection_worker()


def test_paced_authoritative_lane_drains_without_overflow() -> None:
    seen: list[int] = []

    def observer(value: int) -> None:
        sleep(0.0005)
        seen.append(value)

    lane = AuthoritativeEventLane(observer, capacity=4096)
    lane.start()
    try:
        for value in range(500):
            lane.publish(value)
        deadline = monotonic() + 5.0
        while len(seen) < 500 and monotonic() < deadline:
            sleep(0.005)
        assert len(seen) == 500
        metrics = lane.metrics()
        assert metrics["authoritative_lane_overflow"] == 0
        assert metrics["authoritative_lane_depth"] == 0
        assert metrics["authoritative_events_enqueued"] == metrics["authoritative_events_dequeued"] == 500
    finally:
        lane.stop()


def test_terminal_market_data_observer_can_fail_closed_entries() -> None:
    class Warrior:
        def __init__(self) -> None:
            self.reason = None

        def fail_closed_market_data(self, reason: str) -> None:
            self.reason = reason

    warrior = Warrior()
    observer = CompositeMarketEventObserver(None, warrior)  # type: ignore[arg-type]
    observer.fail_closed_market_data("MARKET_DATA_TERMINAL_FAILURE")
    assert warrior.reason == "MARKET_DATA_TERMINAL_FAILURE"
