"""Callable factory for composed paper-runtime drivers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from app.execution_coordinator import ExecutionCoordinator
from app.operations.learning_runtime import RuntimeInferenceAdapter
from app.operations.runtime import (
    CheckpointSink,
    PaperRuntimeCycleResult,
    RequestBuilder,
    RuntimeEventSink,
    SnapshotSource,
)
from app.services.runtime_drivers import PaperRuntimeDriver
from app.strategy_engine import StrategyEngine

from .paper_runtime import Clock, create_paper_runtime_driver


@dataclass(frozen=True, slots=True)
class PaperRuntimeDriverFactory:
    """
    Zero-argument driver factory for RuntimeService.

    RuntimeService owns lifecycle management. This factory retains the
    authoritative paper-runtime dependencies and creates one fresh driver per
    runtime session.
    """

    session_id: str
    initial_cash: Decimal
    snapshot_source: SnapshotSource
    coordinator: ExecutionCoordinator
    request_builder: RequestBuilder
    clock: Clock
    strategy_engine: StrategyEngine | None = None
    inference_adapter: RuntimeInferenceAdapter | None = None
    event_sink: RuntimeEventSink | None = None
    checkpoint_sink: CheckpointSink | None = None
    runtime_result_sink: Callable[[PaperRuntimeCycleResult], None] | None = None
    interval_seconds: float = 1.0
    environment: str = "PAPER"
    active_model: str = "Promoted model"
    experiment_journal: object | None = None

    def __call__(self) -> PaperRuntimeDriver:
        """Create a fresh composed paper-runtime driver."""

        return create_paper_runtime_driver(
            session_id=self.session_id,
            initial_cash=self.initial_cash,
            snapshot_source=self.snapshot_source,
            coordinator=self.coordinator,
            request_builder=self.request_builder,
            clock=self.clock,
            strategy_engine=self.strategy_engine,
            inference_adapter=self.inference_adapter,
            event_sink=self.event_sink,
            checkpoint_sink=self.checkpoint_sink,
            runtime_result_sink=self.runtime_result_sink,
            interval_seconds=self.interval_seconds,
            environment=self.environment,
            active_model=self.active_model,
            **(
                {}
                if self.experiment_journal is None
                else {"experiment_journal": self.experiment_journal}
            ),
        )


__all__ = [
    "PaperRuntimeDriverFactory",
]
