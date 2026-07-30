from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.authentication.models import AuthenticationStatus
from app.composition import create_desktop_composition
from app.gui.main_window import MainWindow
from app.market_data.models import (
    MarketEvent,
    MarketEventType,
    QuotePayload,
)
from app.order_cancellation import OrderCancellationRequest
from app.operations.runtime import PaperRuntimeEvent
from app.paper_trading.command_composition import (
    PAPER_ACCOUNT_ID,
    PAPER_SESSION_ID,
)
from app.paper_trading.models import JournalEventType
from app.services import OrderEntryCommand
from app.session.models import SessionStatus


def quote(
    *,
    sequence: int,
    bid: str = "100",
    ask: str = "101",
    volume: str = "10",
    timestamp: datetime | None = None,
) -> MarketEvent:
    return MarketEvent(
        sequence=sequence,
        timestamp=timestamp or datetime.now(UTC) + timedelta(seconds=1),
        symbol="AAPL",
        source="webull",
        event_type=MarketEventType.QUOTE,
        payload=QuotePayload(
            bid=Decimal(bid),
            ask=Decimal(ask),
            bid_size=Decimal(volume),
            ask_size=Decimal(volume),
        ),
    )


def placement_request(
    composition,
    *,
    quantity: str = "5",
    order_type: str = "MARKET",
    limit_price: Decimal | None = None,
):
    return composition.order_command_factory.create_placement_request(
        OrderEntryCommand(
            symbol="AAPL",
            side="BUY",
            quantity=Decimal(quantity),
            order_type=order_type,
            limit_price=limit_price,
            stop_price=None,
            time_in_force="DAY",
        )
    )


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def test_gui_submission_enters_single_paper_authority_and_presenter(
    application,
) -> None:
    composition = create_desktop_composition()
    window = MainWindow(
        composition.bus,
        composition.state_store,
        composition.runtime_service,
        composition.trading_service,
        composition.order_command_factory,
    )

    try:
        window.orders._place_validated_order(
            {
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 3,
                "order_type": "MARKET",
                "limit_price": None,
                "stop_price": None,
                "time_in_force": "DAY",
            }
        )
        application.processEvents()

        state = composition.state_store.snapshot()
        assert len(composition.paper_order_book) == 1
        assert (
            composition.paper_execution_engine.order_book
            is composition.paper_order_book
        )
        assert len(state.order_projection.orders) == 1
        assert state.order_projection.orders[0].status == "WORKING"
        assert state.portfolio_projection.working_orders == 1
        assert len(state.decision_projection.decisions) == 1
        assert state.decision_projection.decisions[0].strategy_id == (
            "operator-order-entry"
        )
        assert state.decision_projection.decisions[
            0
        ].execution_outcome.value == "ACCEPTED"
        assert window.orders._orders_table.rowCount() == 1
        assert "Submitted:" in (
            window.orders.order_entry_panel._validation_status.text()
        )
    finally:
        window.close()
        composition.close()


def test_partial_and_complete_fills_update_all_existing_projections() -> None:
    composition = create_desktop_composition()
    request = placement_request(composition, quantity="5")

    try:
        composition.runtime_projections.sink(
            PaperRuntimeEvent(
                sequence=100,
                timestamp=datetime.now(UTC),
                event_type="MARK_UPDATED",
                message="Broker market mark.",
                cycle=0,
                symbol="AAPL",
                mark_price=Decimal("100"),
                source="desktop-broker-runtime:test",
            )
        )
        placement = composition.trading_service.place_order(request)
        authority = composition.paper_trading_commands.gateway

        partial = authority.process_market_event(
            quote(sequence=1, volume="2")
        )
        partial_state = composition.state_store.snapshot()

        assert placement.success is True
        assert partial[0].order.status.value == "PARTIALLY_FILLED"
        assert partial_state.order_projection.orders[0].status == (
            "PARTIALLY_FILLED"
        )
        assert partial_state.position_projection.positions[0].quantity == "2"
        assert partial_state.position_projection.positions[0].average_cost == (
            "101"
        )
        assert partial_state.portfolio_projection.open_positions == 1
        assert partial_state.portfolio_projection.working_orders == 1
        assert partial_state.portfolio_projection.total_market_value == "202"
        assert partial_state.decision_projection.decisions[
            0
        ].execution_outcome.value == "PARTIALLY_FILLED"

        filled = authority.process_market_event(
            quote(
                sequence=2,
                ask="102",
                volume="10",
                timestamp=datetime.now(UTC) + timedelta(seconds=2),
            )
        )
        filled_state = composition.state_store.snapshot()

        assert filled[0].order.status.value == "FILLED"
        assert filled_state.order_projection.orders[0].status == "FILLED"
        assert filled_state.position_projection.positions[0].quantity == "5"
        assert filled_state.position_projection.positions[0].average_cost == (
            "101.6"
        )
        assert filled_state.portfolio_projection.working_orders == 0
        assert filled_state.portfolio_projection.open_positions == 1
        assert filled_state.decision_projection.decisions[
            0
        ].execution_outcome.value == "FILLED"
        assert any(
            entry.title == "Order filled"
            for entry in filled_state.timeline_projection.entries
        )
        assert [
            item.event_type
            for item in authority.journal.events
        ] == [
            JournalEventType.PROPOSAL,
            JournalEventType.FILL,
            JournalEventType.FILL,
        ]
    finally:
        composition.close()


def test_cancellation_updates_authority_projection_and_timeline() -> None:
    composition = create_desktop_composition()
    request = placement_request(
        composition,
        order_type="LIMIT",
        limit_price=Decimal("90"),
    )

    try:
        placement = composition.trading_service.place_order(request)
        cancellation = composition.trading_service.cancel_order(
            OrderCancellationRequest(
                request_id="cancel-aapl-1",
                session_id=PAPER_SESSION_ID,
                account_id=PAPER_ACCOUNT_ID,
                broker_order_id=placement.broker_order_id,
                client_order_id=placement.client_order_id,
            )
        )
        state = composition.state_store.snapshot()

        assert cancellation.success is True
        assert composition.paper_order_book.get(
            placement.broker_order_id
        ).status.value == "CANCELLED"
        assert state.order_projection.orders[0].status == "CANCELLED"
        assert state.portfolio_projection.working_orders == 0
        assert state.decision_projection.decisions[
            0
        ].execution_outcome.value == "CANCELLED"
        assert any(
            entry.title == "Order cancelled"
            for entry in state.timeline_projection.entries
        )
        assert composition.paper_trading_commands.gateway.journal.events[
            -1
        ].event_type is JournalEventType.CANCELLATION
    finally:
        composition.close()


def test_duplicate_submission_is_rejected_without_parallel_order() -> None:
    composition = create_desktop_composition()
    request = placement_request(composition)

    try:
        first = composition.trading_service.place_order(request)
        duplicate = composition.trading_service.place_order(request)
        state = composition.state_store.snapshot()

        assert first.success is True
        assert duplicate.success is False
        assert duplicate.gateway_message == "duplicate paper client order ID"
        assert len(composition.paper_order_book) == 1
        assert any(
            order.status == "REJECTED"
            for order in state.order_projection.orders
        )
        assert composition.paper_trading_commands.gateway.journal.events[
            -1
        ].event_type is JournalEventType.REJECTION
    finally:
        composition.close()


def test_paper_sell_without_projected_position_is_rejected() -> None:
    composition = create_desktop_composition()
    request = composition.order_command_factory.create_placement_request(
        OrderEntryCommand(
            symbol="AAPL",
            side="SELL",
            quantity=Decimal("1"),
            order_type="MARKET",
            limit_price=None,
            stop_price=None,
            time_in_force="DAY",
        )
    )

    try:
        result = composition.trading_service.place_order(request)

        assert result.success is False
        assert result.gateway_message == (
            "paper sell quantity exceeds the projected long position"
        )
        assert len(composition.paper_order_book) == 0
        assert composition.state_store.snapshot().order_projection.orders[
            0
        ].status == "REJECTED"
    finally:
        composition.close()


def test_desktop_shutdown_closes_paper_authorization_session() -> None:
    composition = create_desktop_composition()
    commands = composition.paper_trading_commands

    assert commands.session_manager.state().status is SessionStatus.ACTIVE
    assert (
        commands.authentication_service.state().status
        is AuthenticationStatus.AUTHENTICATED
    )

    composition.close()

    assert commands.session_manager.state().status is (
        SessionStatus.INVALIDATED
    )
    assert commands.authentication_service.state().status is (
        AuthenticationStatus.LOGGED_OUT
    )


def test_paper_submission_never_constructs_or_invokes_live_broker(
    monkeypatch,
) -> None:
    import app.composition.desktop_broker_runtime as broker_composition

    invoked = []
    monkeypatch.setattr(
        broker_composition,
        "create_broker_runtime",
        lambda **kwargs: invoked.append(kwargs),
    )
    composition = create_desktop_composition()

    try:
        result = composition.trading_service.place_order(
            placement_request(composition)
        )

        assert result.success is True
        assert invoked == []
        assert composition.paper_trading_commands.gateway.execution_engine is (
            composition.paper_execution_engine
        )
    finally:
        composition.close()
