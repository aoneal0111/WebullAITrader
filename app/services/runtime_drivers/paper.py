from __future__ import annotations

from collections.abc import Callable
from threading import Event

from app.operations import (
    PaperOperationsEngine,
    PaperRuntimeCycleResult,
    PaperRuntimeStatus,
)


EngineFactory = Callable[
    [Callable[[PaperRuntimeCycleResult], None]],
    PaperOperationsEngine,
]


class PaperRuntimeDriver:
    """Adapt PaperOperationsEngine to the RuntimeDriver contract."""

    def __init__(
        self,
        engine_factory: EngineFactory,
        *,
        interval_seconds: float = 1.0,
        environment: str = "PAPER",
        active_model: str = "Promoted model",
        runtime_result_sink: Callable[[PaperRuntimeCycleResult], None] | None = None,
    ) -> None:
        if not callable(engine_factory):
            raise TypeError("engine_factory must be callable")

        if interval_seconds < 0:
            raise ValueError("interval_seconds must be nonnegative")

        if not environment.strip():
            raise ValueError("environment must not be empty")

        if not active_model.strip():
            raise ValueError("active_model must not be empty")

        self._engine_factory = engine_factory
        self._interval_seconds = interval_seconds
        self._environment = environment.strip()
        self._active_model = active_model.strip()
        self._runtime_result_sink = runtime_result_sink
        self._engine: PaperOperationsEngine | None = None

    @property
    def environment(self) -> str:
        return self._environment

    @property
    def active_model(self) -> str:
        return self._active_model

    @property
    def cycles_completed(self) -> int:
        if self._engine is None:
            return 0
        return self._engine.state.cycles_completed

    def run(
        self,
        *,
        stop_event: Event,
        cycle_sink: Callable[[int], None],
    ) -> None:
        if self._engine is not None:
            raise RuntimeError("paper runtime driver can only be run once")

        def publish_cycle(result: PaperRuntimeCycleResult) -> None:
            if self._runtime_result_sink is not None:
                self._runtime_result_sink(result)
            cycle_sink(result.cycle)

        engine = self._engine_factory(publish_cycle)

        if not isinstance(engine, PaperOperationsEngine):
            raise TypeError(
                "engine_factory must return PaperOperationsEngine"
            )

        self._engine = engine
        engine.start()

        try:
            engine.run(
                interval_seconds=self._interval_seconds,
                stop_event=stop_event,
                wait=stop_event.wait,
            )
        finally:
            if engine.state.status in {
                PaperRuntimeStatus.RUNNING,
                PaperRuntimeStatus.PAUSED,
                PaperRuntimeStatus.FAILED,
            }:
                engine.stop()
