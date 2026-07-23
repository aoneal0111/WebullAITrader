"""Composition helpers for paper-runtime production dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.execution_coordinator import ExecutionCoordinator
from app.operations.learning_runtime import (
    FeatureBuilder,
    ReloadableInferenceEngine,
    RuntimeInferenceAdapter,
    RuntimeInferencePolicy,
)
from app.operations.runtime import (
    Clock,
    RequestBuilder,
    SnapshotSource,
)
from app.operations.scanner_runtime import (
    LiveScannerSnapshotSource,
    ScannerCoordinator,
    SnapshotResolver,
)
from app.strategy_engine import StrategyEngine


@dataclass(frozen=True, slots=True)
class PaperRuntimeDependencies:
    """Validated dependencies required to construct a paper runtime driver."""

    snapshot_source: SnapshotSource
    coordinator: ExecutionCoordinator
    request_builder: RequestBuilder
    clock: Clock
    strategy_engine: StrategyEngine
    inference_adapter: RuntimeInferenceAdapter | None = None

def create_execution_coordinator(
    *,
    proposal_factory: Callable[[Any], object],
    risk_evaluator: Callable[[Any], object],
    compliance_evaluator: Callable[[Any], object],
    paper_executor: Callable[[Any], object],
    risk_approved: Callable[[object], bool] | None = None,
    compliance_approved: Callable[[object], bool] | None = None,
    execution_succeeded: Callable[[object], bool] | None = None,
    decision_message: Callable[[object], str] | None = None,
) -> ExecutionCoordinator:
    """Compose the existing strategy-to-paper execution coordinator."""

    for value, name in (
        (proposal_factory, "proposal_factory"),
        (risk_evaluator, "risk_evaluator"),
        (compliance_evaluator, "compliance_evaluator"),
        (paper_executor, "paper_executor"),
    ):
        if not callable(value):
            raise TypeError(f"{name} must be callable")

    return ExecutionCoordinator(
        proposal_factory=proposal_factory,
        risk_evaluator=risk_evaluator,
        compliance_evaluator=compliance_evaluator,
        paper_executor=paper_executor,
        risk_approved=risk_approved,
        compliance_approved=compliance_approved,
        execution_succeeded=execution_succeeded,
        decision_message=decision_message,
    )


def create_live_snapshot_source(
    *,
    coordinator: ScannerCoordinator,
    snapshot_resolver: SnapshotResolver,
    candidate_limit: int = 25,
    maximum_events_per_cycle: int = 1000,
    cycle_sink: Callable[[Any], None] | None = None,
) -> LiveScannerSnapshotSource:
    """Adapt the existing live scanner to the paper-runtime snapshot contract."""

    if not callable(snapshot_resolver):
        raise TypeError("snapshot_resolver must be callable")

    return LiveScannerSnapshotSource(
        coordinator,
        snapshot_resolver,
        candidate_limit=candidate_limit,
        maximum_events_per_cycle=maximum_events_per_cycle,
        cycle_sink=cycle_sink,
    )


def create_runtime_inference_adapter(
    *,
    inference_engine: ReloadableInferenceEngine,
    feature_builder: FeatureBuilder,
    policy: RuntimeInferencePolicy | None = None,
) -> RuntimeInferenceAdapter:
    """Compose promoted-model inference for optional runtime gating."""

    if not callable(feature_builder):
        raise TypeError("feature_builder must be callable")

    return RuntimeInferenceAdapter(
        inference_engine=inference_engine,
        feature_builder=feature_builder,
        policy=policy,
    )


def create_request_builder(
    builder: RequestBuilder,
) -> RequestBuilder:
    """Validate and return an application-supplied coordination request builder.

    The builder remains responsible for supplying authoritative quantities,
    market state, account state, risk limits, compliance limits, and paper
    execution inputs. Composition must not invent those values.
    """

    if not callable(builder):
        raise TypeError("builder must be callable")

    return builder


def create_paper_runtime_dependencies(
    *,
    snapshot_source: SnapshotSource,
    coordinator: ExecutionCoordinator,
    request_builder: RequestBuilder,
    clock: Clock = utc_clock,
    strategy_engine: StrategyEngine | None = None,
    inference_adapter: RuntimeInferenceAdapter | None = None,
) -> PaperRuntimeDependencies:
    """Create a validated dependency bundle for the paper runtime."""

    if not callable(snapshot_source):
        raise TypeError("snapshot_source must be callable")

    if not isinstance(coordinator, ExecutionCoordinator):
        raise TypeError("coordinator must be ExecutionCoordinator")

    validated_request_builder = create_request_builder(request_builder)

    if not callable(clock):
        raise TypeError("clock must be callable")

    configured_strategy = strategy_engine or StrategyEngine()

    if not isinstance(configured_strategy, StrategyEngine):
        raise TypeError("strategy_engine must be StrategyEngine")

    if (
        inference_adapter is not None
        and not isinstance(inference_adapter, RuntimeInferenceAdapter)
    ):
        raise TypeError(
            "inference_adapter must be RuntimeInferenceAdapter or None"
        )

    return PaperRuntimeDependencies(
        snapshot_source=snapshot_source,
        coordinator=coordinator,
        request_builder=validated_request_builder,
        clock=clock,
        strategy_engine=configured_strategy,
        inference_adapter=inference_adapter,
    )


__all__ = [
    "PaperRuntimeDependencies",
    "create_execution_coordinator",
    "create_live_snapshot_source",
    "create_paper_runtime_dependencies",
    "create_request_builder",
    "create_runtime_inference_adapter",
    "utc_clock",
]
