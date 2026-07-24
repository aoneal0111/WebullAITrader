"""Composition helpers for constructing paper runtime driver factories."""

from __future__ import annotations

from decimal import Decimal

from app.operations.runtime import CheckpointSink, RuntimeEventSink

from .paper_dependencies import PaperRuntimeDependencies
from .paper_runtime_driver_factory import PaperRuntimeDriverFactory


def create_paper_runtime_driver_factory(
    *,
    session_id: str,
    initial_cash: Decimal,
    dependencies: PaperRuntimeDependencies,
    event_sink: RuntimeEventSink | None = None,
    checkpoint_sink: CheckpointSink | None = None,
    interval_seconds: float = 1.0,
    environment: str = "PAPER",
    active_model: str = "Promoted model",
) -> PaperRuntimeDriverFactory:
    """Create a PaperRuntimeDriverFactory from validated dependencies."""

    if not isinstance(dependencies, PaperRuntimeDependencies):
        raise TypeError(
            "dependencies must be PaperRuntimeDependencies"
        )

    return PaperRuntimeDriverFactory(
        session_id=session_id,
        initial_cash=initial_cash,
        snapshot_source=dependencies.snapshot_source,
        coordinator=dependencies.coordinator,
        request_builder=dependencies.request_builder,
        clock=dependencies.clock,
        strategy_engine=dependencies.strategy_engine,
        inference_adapter=dependencies.inference_adapter,
        event_sink=event_sink,
        checkpoint_sink=checkpoint_sink,
        interval_seconds=interval_seconds,
        environment=environment,
        active_model=active_model,
    )


__all__ = [
    "create_paper_runtime_driver_factory",
]
