from __future__ import annotations

from collections.abc import Callable
from threading import Event
from time import monotonic, sleep

import pytest

from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    RuntimeFailed,
    RuntimePhase,
    RuntimeStarted,
    RuntimeStopped,
)
from app.services import (
    RuntimeService,
    RuntimeServiceStatus,
    SimulatedPaperRuntimeDriver,
)


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_seconds: float = 1.0,
) -> None:
    deadline = monotonic() + timeout_seconds

    while monotonic() < deadline:
        if predicate():
            return

        sleep(0.005)

    raise AssertionError("condition was not reached before timeout")


def test_runtime_service_starts_and_stops_driver() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    service = RuntimeService(
        bus,
        lambda: SimulatedPaperRuntimeDriver(
            interval_seconds=0.005,
            active_model="test-model",
        ),
    )

    assert service.start() is True

    wait_until(
        lambda: store.snapshot().runtime.cycles_completed >= 2
    )

    assert service.status is RuntimeServiceStatus.RUNNING
    assert store.snapshot().runtime.phase is RuntimePhase.RUNNING
    assert store.snapshot().runtime.active_model == "test-model"

    assert service.stop("Test complete.") is True
    assert service.wait(1.0) is True

    snapshot = store.snapshot()

    assert service.status is RuntimeServiceStatus.STOPPED
    assert snapshot.runtime.phase is RuntimePhase.STOPPED
    assert snapshot.runtime.cycles_completed >= 2
    assert snapshot.timeline[-1].event_type == "RuntimeStopped"


def test_runtime_service_rejects_duplicate_start() -> None:
    bus = OperationsBus()

    service = RuntimeService(
        bus,
        lambda: SimulatedPaperRuntimeDriver(
            interval_seconds=0.05,
        ),
    )

    assert service.start() is True
    assert service.start() is False

    service.stop()
    assert service.wait(1.0) is True


def test_runtime_service_stop_is_idempotent() -> None:
    bus = OperationsBus()

    service = RuntimeService(
        bus,
        lambda: SimulatedPaperRuntimeDriver(
            interval_seconds=0.05,
        ),
    )

    assert service.stop() is False

    service.start()

    assert service.stop("First stop.") is True
    assert service.stop("Second stop.") is True
    assert service.wait(1.0) is True


def test_runtime_service_creates_new_driver_for_each_session() -> None:
    bus = OperationsBus()
    created: list[SimulatedPaperRuntimeDriver] = []

    def factory() -> SimulatedPaperRuntimeDriver:
        driver = SimulatedPaperRuntimeDriver(
            interval_seconds=0.005,
        )
        created.append(driver)
        return driver

    service = RuntimeService(bus, factory)

    service.start()
    wait_until(lambda: service.cycles_completed >= 1)
    service.stop()
    assert service.wait(1.0)

    service.start()
    wait_until(lambda: service.cycles_completed >= 1)
    service.stop()
    assert service.wait(1.0)

    assert len(created) == 2
    assert created[0] is not created[1]


def test_runtime_service_publishes_driver_failure() -> None:
    class FailingDriver:
        environment = "PAPER"
        active_model = "failure-model"
        cycles_completed = 0

        def run(
            self,
            *,
            stop_event: Event,
            cycle_sink: Callable[[int], None],
        ) -> None:
            raise RuntimeError("driver exploded")

    bus = OperationsBus()
    failures: list[RuntimeFailed] = []

    bus.subscribe(RuntimeFailed, failures.append)

    service = RuntimeService(bus, FailingDriver)

    assert service.start() is True
    assert service.wait(1.0) is True

    assert len(failures) == 1
    assert "driver exploded" in failures[0].error_message
    assert service.status is RuntimeServiceStatus.STOPPED


def test_runtime_service_publishes_one_start_and_stop() -> None:
    bus = OperationsBus()
    started: list[RuntimeStarted] = []
    stopped: list[RuntimeStopped] = []

    bus.subscribe(RuntimeStarted, started.append)
    bus.subscribe(RuntimeStopped, stopped.append)

    service = RuntimeService(
        bus,
        lambda: SimulatedPaperRuntimeDriver(
            interval_seconds=0.005,
        ),
    )

    service.start()
    wait_until(lambda: service.cycles_completed >= 1)
    service.stop("Operator test.")
    assert service.wait(1.0)

    assert len(started) == 1
    assert len(stopped) == 1
    assert stopped[0].reason == "Operator test."


def test_runtime_service_validates_stop_reason() -> None:
    bus = OperationsBus()
    service = RuntimeService(
        bus,
        SimulatedPaperRuntimeDriver,
    )

    service.start()

    with pytest.raises(ValueError, match="stop reason"):
        service.stop("   ")

    service.stop()
    assert service.wait(1.0)


def test_simulated_driver_rejects_negative_interval() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        SimulatedPaperRuntimeDriver(interval_seconds=-1)
