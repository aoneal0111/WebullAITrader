from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import app.composition.desktop_runtime_bootstrap as bootstrap_module
from app.composition.desktop_runtime_bootstrap import (
    DesktopRuntimeBootstrap,
    create_desktop_runtime_bootstrap,
)


def test_create_desktop_runtime_bootstrap_wires_existing_composition_layers(
    monkeypatch,
) -> None:
    scanner_infrastructure = object()
    runtime_dependencies = object()
    driver_factory = object()

    scanner_calls: list[dict[str, object]] = []
    dependency_calls: list[dict[str, object]] = []
    factory_calls: list[dict[str, object]] = []

    def fake_create_scanner(**kwargs):
        scanner_calls.append(kwargs)
        return scanner_infrastructure

    def fake_create_dependencies(**kwargs):
        dependency_calls.append(kwargs)
        return runtime_dependencies

    def fake_create_factory(**kwargs):
        factory_calls.append(kwargs)
        return driver_factory

    monkeypatch.setattr(
        bootstrap_module,
        "create_desktop_scanner_infrastructure",
        fake_create_scanner,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "create_desktop_paper_runtime_dependencies",
        fake_create_dependencies,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "create_paper_runtime_driver_factory",
        fake_create_factory,
    )

    clock = lambda: datetime(2026, 7, 24, tzinfo=UTC)
    market_data_client = Mock()
    universe_service = Mock()
    reference_data_service = Mock()
    scanner_adapter = Mock()
    snapshot_resolver = Mock()
    quantity_provider = Mock()
    request_id_provider = Mock()
    runtime_context_configuration = Mock()
    timestamp_source = Mock()
    market_state_source = Mock()
    market_quote_source = Mock()
    gfv_decision_source = Mock()
    reference_sink = Mock()
    strategy_engine = Mock()
    inference_adapter = Mock()
    event_sink = Mock()
    checkpoint_sink = Mock()

    result = create_desktop_runtime_bootstrap(
        market_data_client=market_data_client,
        universe_service=universe_service,
        reference_data_service=reference_data_service,
        scanner_adapter=scanner_adapter,
        snapshot_resolver=snapshot_resolver,
        quantity_provider=quantity_provider,
        request_id_provider=request_id_provider,
        runtime_context_configuration=runtime_context_configuration,
        timestamp_source=timestamp_source,
        market_state_source=market_state_source,
        market_quote_source=market_quote_source,
        gfv_decision_source=gfv_decision_source,
        clock=clock,
        session_id="desktop-paper-session",
        initial_cash=Decimal("25000"),
        reference_sink=reference_sink,
        default_channels=("quotes", "trades"),
        maximum_events_per_cycle=250,
        candidate_limit=15,
        strategy_engine=strategy_engine,
        inference_adapter=inference_adapter,
        event_sink=event_sink,
        checkpoint_sink=checkpoint_sink,
        interval_seconds=2.5,
        environment="PAPER",
        active_model="production-model",
    )

    assert isinstance(result, DesktopRuntimeBootstrap)
    assert result.scanner_infrastructure is scanner_infrastructure
    assert result.runtime_dependencies is runtime_dependencies
    assert result.driver_factory is driver_factory

    assert scanner_calls == [
        {
            "market_data_client": market_data_client,
            "universe_service": universe_service,
            "reference_data_service": reference_data_service,
            "scanner_adapter": scanner_adapter,
            "scanner_config": bootstrap_module.MomentumScannerConfig(),
            "reference_sink": reference_sink,
            "clock": clock,
            "default_channels": ("quotes", "trades"),
            "maximum_events_per_cycle": 250,
        }
    ]

    assert dependency_calls == [
        {
            "scanner_infrastructure": scanner_infrastructure,
            "snapshot_resolver": snapshot_resolver,
            "quantity_provider": quantity_provider,
            "request_id_provider": request_id_provider,
            "runtime_context_configuration": runtime_context_configuration,
            "timestamp_source": timestamp_source,
            "market_state_source": market_state_source,
            "market_quote_source": market_quote_source,
            "gfv_decision_source": gfv_decision_source,
            "clock": clock,
            "strategy_engine": strategy_engine,
            "inference_adapter": inference_adapter,
            "candidate_limit": 15,
            "maximum_events_per_cycle": 250,
        }
    ]

    assert factory_calls == [
        {
            "session_id": "desktop-paper-session",
            "initial_cash": Decimal("25000"),
            "dependencies": runtime_dependencies,
            "event_sink": event_sink,
            "checkpoint_sink": checkpoint_sink,
            "runtime_result_sink": None,
            "interval_seconds": 2.5,
            "environment": "PAPER",
            "active_model": "production-model",
        }
    ]


def test_create_desktop_runtime_bootstrap_does_not_start_infrastructure(
    monkeypatch,
) -> None:
    scanner_infrastructure = Mock()
    runtime_dependencies = object()
    driver_factory = object()

    monkeypatch.setattr(
        bootstrap_module,
        "create_desktop_scanner_infrastructure",
        Mock(return_value=scanner_infrastructure),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "create_desktop_paper_runtime_dependencies",
        Mock(return_value=runtime_dependencies),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "create_paper_runtime_driver_factory",
        Mock(return_value=driver_factory),
    )

    create_desktop_runtime_bootstrap(
        market_data_client=Mock(),
        universe_service=Mock(),
        reference_data_service=Mock(),
        scanner_adapter=Mock(),
        snapshot_resolver=Mock(),
        quantity_provider=Mock(),
        request_id_provider=Mock(),
        runtime_context_configuration=Mock(),
        timestamp_source=Mock(),
        market_state_source=Mock(),
        market_quote_source=Mock(),
        gfv_decision_source=Mock(),
        clock=lambda: datetime(2026, 7, 24, tzinfo=UTC),
        session_id="desktop-paper-session",
        initial_cash=Decimal("25000"),
    )

    scanner_infrastructure.coordinator.connect.assert_not_called()
    scanner_infrastructure.coordinator.subscribe.assert_not_called()
    scanner_infrastructure.coordinator.run_available.assert_not_called()
