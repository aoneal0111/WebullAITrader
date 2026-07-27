from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

import app.composition.paper_runtime_composition as composition_module
from app.composition.paper_dependencies import PaperRuntimeDependencies
from app.composition.paper_runtime_composition import (
    create_paper_runtime_driver_factory,
)


def test_create_paper_runtime_driver_factory_forwards_dependencies(
    monkeypatch,
) -> None:
    captured: list[dict[str, object]] = []
    created_factory = object()

    def fake_factory(**kwargs):
        captured.append(kwargs)
        return created_factory

    monkeypatch.setattr(
        composition_module,
        "PaperRuntimeDriverFactory",
        fake_factory,
    )

    snapshot_source = lambda: object()
    coordinator = object()
    request_builder = lambda decision, snapshot, state: object()
    clock = lambda: datetime(2026, 7, 23, tzinfo=UTC)
    strategy_engine = object()
    inference_adapter = object()
    event_sink = lambda event: None
    checkpoint_sink = lambda checkpoint: None

    dependencies = PaperRuntimeDependencies(
        snapshot_source=snapshot_source,
        coordinator=coordinator,
        request_builder=request_builder,
        clock=clock,
        strategy_engine=strategy_engine,
        inference_adapter=inference_adapter,
    )

    result = create_paper_runtime_driver_factory(
        session_id="paper-session",
        initial_cash=Decimal("25000"),
        dependencies=dependencies,
        event_sink=event_sink,
        checkpoint_sink=checkpoint_sink,
        interval_seconds=2.5,
        environment="PAPER",
        active_model="production-model",
    )

    assert result is created_factory
    assert captured == [
        {
            "session_id": "paper-session",
            "initial_cash": Decimal("25000"),
            "snapshot_source": snapshot_source,
            "coordinator": coordinator,
            "request_builder": request_builder,
            "clock": clock,
            "strategy_engine": strategy_engine,
            "inference_adapter": inference_adapter,
            "event_sink": event_sink,
            "checkpoint_sink": checkpoint_sink,
                "runtime_result_sink": None,
            "interval_seconds": 2.5,
            "environment": "PAPER",
            "active_model": "production-model",
        }
    ]


def test_create_paper_runtime_driver_factory_rejects_invalid_dependencies() -> None:
    with pytest.raises(
        TypeError,
        match="dependencies must be PaperRuntimeDependencies",
    ):
        create_paper_runtime_driver_factory(
            session_id="paper-session",
            initial_cash=Decimal("25000"),
            dependencies=object(),
        )
