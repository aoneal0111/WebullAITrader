from __future__ import annotations

from threading import Event
from types import SimpleNamespace

import pytest

import app.services.runtime_drivers.paper as driver_module
from app.operations import PaperRuntimeStatus
from app.services.runtime_drivers.paper import PaperRuntimeDriver


class FakePaperOperationsEngine:
    def __init__(self, result_sink) -> None:
        self._result_sink = result_sink
        self.state = SimpleNamespace(
            cycles_completed=0,
            status=PaperRuntimeStatus.STOPPED,
        )

    def start(self) -> None:
        self.state.status = PaperRuntimeStatus.RUNNING

    def run(
        self,
        *,
        interval_seconds: float,
        stop_event: Event,
        wait,
    ) -> None:
        result = SimpleNamespace(cycle=7)
        self._result_sink(result)
        self.state.cycles_completed = 1
        self.state.status = PaperRuntimeStatus.STOPPED

    def stop(self) -> None:
        self.state.status = PaperRuntimeStatus.STOPPED


def test_driver_publishes_full_result_and_cycle_number(monkeypatch) -> None:
    monkeypatch.setattr(
        driver_module,
        "PaperOperationsEngine",
        FakePaperOperationsEngine,
    )

    published_results: list[object] = []
    published_cycles: list[int] = []

    driver = PaperRuntimeDriver(
        FakePaperOperationsEngine,
        interval_seconds=0,
        runtime_result_sink=published_results.append,
    )

    driver.run(
        stop_event=Event(),
        cycle_sink=published_cycles.append,
    )

    assert len(published_results) == 1
    assert published_results[0].cycle == 7
    assert published_cycles == [7]
    assert driver.cycles_completed == 1


def test_driver_rejects_invalid_runtime_result_sink() -> None:
    with pytest.raises(
        TypeError,
        match="runtime_result_sink must be callable",
    ):
        PaperRuntimeDriver(
            FakePaperOperationsEngine,
            runtime_result_sink=object(),
        )
