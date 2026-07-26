"""Composition helpers for the deterministic paper-trading runtime."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from app.execution_coordinator import ExecutionCoordinator
from app.operations.learning_runtime import RuntimeInferenceAdapter
from app.operations.runtime import (
    CheckpointSink,
    PaperOperationsEngine,
    PaperRuntimeCycleResult,
    RequestBuilder,
    RuntimeEventSink,
    SnapshotSource,
)
from app.services.runtime_drivers import PaperRuntimeDriver
from app.strategy_engine import StrategyEngine


Clock = Callable[[], datetime]


def create_paper_runtime_driver(
    *,
    session_id: str,
    initial_cash: Decimal,
    snapshot_source: SnapshotSource,
    coordinator: ExecutionCoordinator,
    request_builder: RequestBuilder,
    clock: Clock,
    strategy_engine: StrategyEngine | None = None,
    inference_adapter: RuntimeInferenceAdapter | None = None,
    event_sink: RuntimeEventSink | None = None,
    checkpoint_sink: CheckpointSink | None = None,
    interval_seconds: float = 1.0,
    environment: str = "PAPER",
    active_model: str = "Promoted model",
    runtime_result_sink: Callable[[PaperRuntimeCycleResult], None] | None = None,
) -> PaperRuntimeDriver:
    """Create a runtime driver with a fresh engine for each runtime session."""

    normalized_session_id = session_id.strip()
    if not normalized_session_id:
        raise ValueError("session_id must not be empty")

    if initial_cash < Decimal("0"):
        raise ValueError("initial_cash must be nonnegative")

    if not callable(snapshot_source):
        raise TypeError("snapshot_source must be callable")

    if not isinstance(coordinator, ExecutionCoordinator):
        raise TypeError("coordinator must be ExecutionCoordinator")

    if not callable(request_builder):
        raise TypeError("request_builder must be callable")

    if not callable(clock):
        raise TypeError("clock must be callable")

    configured_strategy = strategy_engine or StrategyEngine()

    def create_engine(
        cycle_sink: Callable[[PaperRuntimeCycleResult], None],
    ) -> PaperOperationsEngine:
        return PaperOperationsEngine(
            session_id=normalized_session_id,
            initial_cash=initial_cash,
            snapshot_source=snapshot_source,
            strategy_engine=configured_strategy,
            coordinator=coordinator,
            request_builder=request_builder,
            clock=clock,
            event_sink=event_sink,
            checkpoint_sink=checkpoint_sink,
            cycle_sink=cycle_sink,
            inference_adapter=inference_adapter,
        )

    return PaperRuntimeDriver(
        create_engine,
        interval_seconds=interval_seconds,
        environment=environment,
        active_model=active_model,
        runtime_result_sink=runtime_result_sink,
    )


__all__ = [
    "create_paper_runtime_driver",
]
