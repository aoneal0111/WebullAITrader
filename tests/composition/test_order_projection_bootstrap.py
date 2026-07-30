from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import app.composition.desktop_runtime_bootstrap as bootstrap_module
from app.composition.runtime_event_sink import CompositeRuntimeEventSink
from app.operations_core import OperationsBus
from app.read_models.order_projection import OrderProjection
from app.read_models.position_projection import PositionProjection
from app.read_models.timeline_projection import TimelineProjection
from app.read_models.decision_projection import DecisionProjection
from app.read_models.portfolio_projection import PortfolioProjection
from app.read_models.health_projection import HealthProjection


def test_desktop_bootstrap_composes_order_projection_with_runtime_sinks(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        bootstrap_module,
        "create_desktop_scanner_infrastructure",
        Mock(return_value=object()),
    )
    monkeypatch.setattr(
        bootstrap_module,
        "create_desktop_paper_runtime_dependencies",
        Mock(return_value=object()),
    )
    create_factory = Mock(return_value=object())
    monkeypatch.setattr(
        bootstrap_module,
        "create_paper_runtime_driver_factory",
        create_factory,
    )
    existing_sink = Mock()
    additional_sink = Mock()

    result = bootstrap_module.create_desktop_runtime_bootstrap(
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
        clock=lambda: datetime(2026, 7, 30, tzinfo=UTC),
        session_id="paper-session",
        initial_cash=Decimal("10000"),
        event_sink=existing_sink,
        event_sinks=(additional_sink,),
        operations_bus=OperationsBus(),
    )

    composed = create_factory.call_args.kwargs["event_sink"]
    assert isinstance(composed, CompositeRuntimeEventSink)
    assert isinstance(result.order_projection, OrderProjection)
    assert isinstance(result.position_projection, PositionProjection)
    assert isinstance(result.timeline_projection, TimelineProjection)
    assert isinstance(result.decision_projection, DecisionProjection)
    assert isinstance(result.portfolio_projection, PortfolioProjection)
    assert isinstance(result.health_projection, HealthProjection)
    assert composed.sinks == (
        existing_sink,
        result.order_projection,
        result.position_projection,
        result.portfolio_projection,
        result.health_projection,
        result.timeline_projection,
        result.decision_projection,
        additional_sink,
    )
