from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import app.composition.paper_runtime_driver_factory as factory_module
from app.composition.paper_runtime_driver_factory import (
    PaperRuntimeDriverFactory,
)


def test_factory_delegates_all_runtime_dependencies(monkeypatch) -> None:
    captured: list[dict[str, object]] = []
    created_driver = object()

    def fake_create_paper_runtime_driver(**kwargs):
        captured.append(kwargs)
        return created_driver

    monkeypatch.setattr(
        factory_module,
        "create_paper_runtime_driver",
        fake_create_paper_runtime_driver,
    )

    snapshot_source = lambda: object()
    coordinator = object()
    request_builder = lambda decision, snapshot, state: object()
    clock = lambda: datetime(2026, 7, 23, tzinfo=UTC)
    strategy_engine = object()
    inference_adapter = object()
    event_sink = lambda event: None
    checkpoint_sink = lambda checkpoint: None

    factory = PaperRuntimeDriverFactory(
        session_id="paper-session",
        initial_cash=Decimal("25000"),
        snapshot_source=snapshot_source,
        coordinator=coordinator,
        request_builder=request_builder,
        clock=clock,
        strategy_engine=strategy_engine,
        inference_adapter=inference_adapter,
        event_sink=event_sink,
        checkpoint_sink=checkpoint_sink,
        interval_seconds=2.5,
        environment="PAPER",
        active_model="production-model",
    )

    result = factory()

    assert result is created_driver
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
            "interval_seconds": 2.5,
            "environment": "PAPER",
            "active_model": "production-model",
        }
    ]


def test_factory_creates_a_driver_for_each_call(monkeypatch) -> None:
    created_drivers = [object(), object()]
    call_count = 0

    def fake_create_paper_runtime_driver(**kwargs):
        nonlocal call_count
        result = created_drivers[call_count]
        call_count += 1
        return result

    monkeypatch.setattr(
        factory_module,
        "create_paper_runtime_driver",
        fake_create_paper_runtime_driver,
    )

    factory = PaperRuntimeDriverFactory(
        session_id="paper-session",
        initial_cash=Decimal("25000"),
        snapshot_source=lambda: object(),
        coordinator=object(),
        request_builder=lambda decision, snapshot, state: object(),
        clock=lambda: datetime(2026, 7, 23, tzinfo=UTC),
    )

    assert factory() is created_drivers[0]
    assert factory() is created_drivers[1]
    assert call_count == 2