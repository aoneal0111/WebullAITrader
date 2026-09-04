from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.composition.runtime_projection_pipeline import (
    create_runtime_projection_pipeline,
)
from app.gui.projections.dashboard_projection import project_dashboard
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.operations_core import ApplicationStateStore, OperationsBus
from app.paper_gateway import PaperOrderGateway
from app.paper_gateway.gateway import PaperDurabilityError
from app.paper_gateway.durable_store import DurablePaperExecutionStore
from app.paper_gateway.order_validity import atlas_day_expiration
from app.paper_trading.command_composition import (
    PAPER_ACCOUNT_ID,
    create_paper_trading_command_composition,
)
from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import OrderStatus, OrderTerminalReason, TimeInForce
from app.services.order_command_factory import OrderEntryCommand
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousManagementReadiness,
    AutonomousPaperExecutionBridge,
    AutonomousPaperReadiness,
)


ET = ZoneInfo("America/New_York")
START = datetime(2026, 9, 3, 10, 0, tzinfo=ET)


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@dataclass(frozen=True)
class Signal:
    symbol: str = "PMI"
    entry_trigger: Decimal = Decimal("10")
    stop_price: Decimal = Decimal("9.50")
    lifecycle_id: str = (
        "WARRIOR_MOMENTUM_V1|PMI|2026-09-03 14:00:00+00:00|"
        "HIGH_OF_DAY_BREAKOUT|10|9.50"
    )


def bridge(composition, **kwargs) -> AutonomousPaperExecutionBridge:
    return AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        **kwargs,
    )


def quote(
    at: datetime,
    *,
    symbol: str = "PMI",
    bid: str = "9.99",
    ask: str = "10",
    size: str = "100",
) -> MarketEvent:
    return MarketEvent(
        1,
        at,
        symbol,
        "test",
        MarketEventType.QUOTE,
        QuotePayload(
            Decimal(bid),
            Decimal(ask),
            Decimal(size),
            Decimal(size),
        ),
        received_timestamp=at,
    )


def submit_entry(composition, signal: Signal = Signal(), quantity: int = 100):
    execution = bridge(composition)
    assert execution.submit_entry(signal, quantity, Decimal("50"))
    return execution, composition.order_book.open_orders()[0]


def submit_manual(composition, *, tif: str = "DAY"):
    result = composition.trading_service.place_order(
        composition.order_command_factory.create_placement_request(
            OrderEntryCommand(
                symbol="MANU",
                side="BUY",
                quantity=Decimal("10"),
                order_type="LIMIT",
                limit_price=Decimal("5"),
                stop_price=None,
                time_in_force=tif,
            )
        )
    )
    assert result.success
    return composition.order_book.get(result.broker_order_id)


def test_same_session_restart_keeps_valid_day_entry_working(tmp_path) -> None:
    path = tmp_path / "same-session.sqlite3"
    clock = Clock(START)
    first = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    _, order = submit_entry(first)
    assert order.request.entry_valid_until == START + timedelta(minutes=1)
    first.close()

    clock.value = START + timedelta(seconds=30)
    events = []
    restarted = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock, event_sink=events.append,
    )
    try:
        restored = restarted.order_book.get(order.order_id)
        assert restored.status is OrderStatus.ACCEPTED
        assert len(restarted.order_book.open_orders()) == 1
        assert not any(event.event_type == "ORDER_EXPIRED" for event in events)
    finally:
        restarted.close()


def test_day_uses_extended_hours_boundary_and_preserves_manual_gtc() -> None:
    clock = Clock(datetime(2026, 9, 3, 19, 59, tzinfo=ET))
    composition = create_paper_trading_command_composition(clock=clock)
    day = submit_manual(composition)
    gtc = submit_manual(composition, tif="GTC")
    assert composition.gateway.reconcile_temporal_validity(at=clock.value) == ()

    clock.value = datetime(2026, 9, 3, 20, 0, tzinfo=ET)
    events = composition.gateway.reconcile_temporal_validity(at=clock.value)
    assert [event.order.order_id for event in events] == [day.order_id]
    assert composition.order_book.get(day.order_id).terminal_reason is OrderTerminalReason.DAY_EXPIRED
    assert not composition.order_book.get(gtc.order_id).is_terminal
    composition.close()


def test_day_submitted_on_known_holiday_expires_immediately() -> None:
    clock = Clock(datetime(2026, 12, 25, 10, 0, tzinfo=ET))
    composition = create_paper_trading_command_composition(clock=clock)
    order = submit_manual(composition)
    assert composition.order_book.get(order.order_id).status is OrderStatus.EXPIRED
    assert (
        composition.order_book.get(order.order_id).terminal_reason
        is OrderTerminalReason.DAY_EXPIRED
    )
    composition.close()


def test_day_submitted_outside_supported_extended_hours_expires_immediately() -> None:
    for submitted in (
        datetime(2026, 9, 3, 3, 59, tzinfo=ET),
        datetime(2026, 9, 3, 20, 0, tzinfo=ET),
    ):
        composition = create_paper_trading_command_composition(
            clock=Clock(submitted),
        )
        order = submit_manual(composition)
        assert composition.order_book.get(order.order_id).status is OrderStatus.EXPIRED
        assert (
            composition.order_book.get(order.order_id).terminal_reason
            is OrderTerminalReason.DAY_EXPIRED
        )
        composition.close()


@pytest.mark.parametrize(
    ("submitted_time", "working"),
    (
        ((3, 59, 59), False),
        ((4, 0, 0), True),
        ((19, 59, 59), True),
        ((20, 0, 0), False),
        ((20, 0, 1), False),
    ),
)
def test_atlas_day_boundaries_are_exact_to_the_second(
    submitted_time: tuple[int, int, int], working: bool,
) -> None:
    clock = Clock(datetime(2026, 9, 3, *submitted_time, tzinfo=ET))
    composition = create_paper_trading_command_composition(clock=clock)
    try:
        order = submit_manual(composition)
        assert (not order.is_terminal) is working
        assert (
            order.terminal_reason is OrderTerminalReason.DAY_EXPIRED
        ) is (not working)
    finally:
        composition.close()


@pytest.mark.parametrize(
    ("submitted", "expected_offset_hours"),
    (
        (datetime(2026, 1, 15, 14, 0, tzinfo=ET), -5),
        (datetime(2026, 9, 3, 14, 0, tzinfo=ET), -4),
    ),
)
def test_atlas_day_expiration_preserves_local_boundary_across_dst(
    submitted: datetime, expected_offset_hours: int,
) -> None:
    clock = Clock(submitted)
    composition = create_paper_trading_command_composition(clock=clock)
    try:
        order = submit_manual(composition)
        expiration = atlas_day_expiration(order)
        assert expiration.astimezone(ET).time() == datetime.min.time().replace(hour=20)
        assert expiration.utcoffset() == timedelta(hours=expected_offset_hours)
    finally:
        composition.close()


def test_friday_day_order_expires_on_weekend_restart(tmp_path) -> None:
    path = tmp_path / "weekend.sqlite3"
    clock = Clock(datetime(2026, 9, 4, 19, 59, tzinfo=ET))
    first = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    order = submit_manual(first)
    first.close()

    clock.value = datetime(2026, 9, 5, 9, 0, tzinfo=ET)
    restarted = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    assert restarted.order_book.get(order.order_id).status is OrderStatus.EXPIRED
    assert (
        restarted.order_book.get(order.order_id).terminal_reason
        is OrderTerminalReason.DAY_EXPIRED
    )
    restarted.close()


def test_chpt_next_day_restart_expires_before_marketable_quote(tmp_path) -> None:
    path = tmp_path / "chpt.sqlite3"
    submitted = datetime(2026, 9, 3, 17, 9, tzinfo=ET)
    clock = Clock(submitted)
    first = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    chpt = Signal(
        symbol="CHPT",
        entry_trigger=Decimal("9.104550"),
        stop_price=Decimal("9.05"),
        lifecycle_id=(
            "WARRIOR_MOMENTUM_V1|CHPT|2026-09-03 21:09:01.281000+00:00|"
            "HIGH_OF_DAY_BREAKOUT|9.104550|9.05"
        ),
    )
    _, order = submit_entry(first, chpt, 1833)
    first.close()

    clock.value = datetime(2026, 9, 4, 4, 0, tzinfo=ET)
    events = []
    restarted = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock, event_sink=events.append,
    )
    try:
        expired = restarted.order_book.get(order.order_id)
        assert expired.status is OrderStatus.EXPIRED
        assert expired.terminal_reason is OrderTerminalReason.DAY_EXPIRED
        assert expired.filled_quantity == 0
        assert expired.remaining_quantity == Decimal("1833")
        assert events[-1].event_type == "ORDER_EXPIRED"
        assert events[-1].order.execution_reason == "DAY_EXPIRED"

        reports = restarted.gateway.process_market_event(quote(
            clock.value,
            symbol="CHPT",
            bid="9.09",
            ask="9.10",
            size="1833",
        ))
        assert reports == ()
        assert expired.fills == ()
    finally:
        restarted.close()


def test_same_session_stale_entry_expires_before_later_matching() -> None:
    clock = Clock(START)
    events = []
    composition = create_paper_trading_command_composition(
        clock=clock, event_sink=events.append,
    )
    _, order = submit_entry(composition)
    clock.value = START + timedelta(minutes=1)

    assert composition.gateway.process_market_event(quote(clock.value)) == ()
    expired = composition.order_book.get(order.order_id)
    assert expired.status is OrderStatus.EXPIRED
    assert expired.terminal_reason is OrderTerminalReason.ENTRY_STALE
    assert expired.fills == ()
    assert events[-1].order.execution_reason == "ENTRY_STALE"
    composition.close()


def test_structural_stop_invalidation_remains_distinct() -> None:
    clock = Clock(START)
    events = []
    composition = create_paper_trading_command_composition(
        clock=clock, event_sink=events.append,
    )
    _, order = submit_entry(composition)
    clock.value = START + timedelta(seconds=10)

    assert composition.gateway.process_market_event(quote(
        clock.value, bid="9.39", ask="9.40",
    )) == ()
    cancelled = composition.order_book.get(order.order_id)
    assert cancelled.status is OrderStatus.CANCELLED
    assert (
        cancelled.terminal_reason
        is OrderTerminalReason.STRUCTURAL_STOP_INVALIDATED
    )
    assert events[-1].order.execution_reason == "STRUCTURAL_STOP_INVALIDATED"
    composition.close()


def test_operator_cancellation_reason_is_durable_and_visible(tmp_path) -> None:
    path = tmp_path / "operator-cancel.sqlite3"
    clock = Clock(START)
    events = []
    composition = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock, event_sink=events.append,
    )
    order = submit_manual(composition)
    request = composition.order_command_factory.create_cancellation_request(
        order.order_id,
        order.request.client_order_id,
        source="operator",
    )
    assert composition.trading_service.cancel_order(request).success
    cancelled = composition.order_book.get(order.order_id)
    assert cancelled.terminal_reason is OrderTerminalReason.OPERATOR_CANCELLED
    assert events[-1].event_type == "ORDER_CANCELLED"
    assert events[-1].order.execution_reason == "OPERATOR_CANCELLED"
    composition.close()

    restarted = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    assert (
        restarted.order_book.get(order.order_id).terminal_reason
        is OrderTerminalReason.OPERATOR_CANCELLED
    )
    restarted.close()


def test_partial_entry_expiry_preserves_fill_and_lifecycle_ownership() -> None:
    clock = Clock(START)
    composition = create_paper_trading_command_composition(clock=clock)
    execution, order = submit_entry(composition)
    clock.value = START + timedelta(seconds=10)
    reports = composition.gateway.process_market_event(quote(
        clock.value, size="40",
    ))
    assert reports[0].order.filled_quantity == Decimal("40")

    clock.value = START + timedelta(minutes=1)
    assert composition.gateway.process_market_event(quote(clock.value)) == ()
    expired = composition.order_book.get(order.order_id)
    assert expired.status is OrderStatus.EXPIRED
    assert expired.terminal_reason is OrderTerminalReason.ENTRY_STALE
    assert expired.filled_quantity == Decimal("40")
    assert expired.remaining_quantity == Decimal("60")
    assert execution.has_execution_ownership("PMI")
    assert execution.ensure_exit(
        "PMI", 40, Decimal("9.50"), "STOP", Signal().lifecycle_id,
    ).protection_active
    composition.close()


@pytest.mark.parametrize(
    ("reason", "order_type"),
    (("STOP", "STOP"), ("TARGET", "LIMIT")),
)
def test_open_position_management_survives_session_boundary(
    tmp_path, reason, order_type,
) -> None:
    path = tmp_path / f"management-{reason}.sqlite3"
    clock = Clock(START)
    first = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    execution, _ = submit_entry(first)
    clock.value = START + timedelta(seconds=10)
    first.gateway.process_market_event(quote(clock.value))
    price = Decimal("9.50") if reason == "STOP" else Decimal("10.50")
    decision = execution.ensure_exit(
        "PMI", 100, price, reason, Signal().lifecycle_id,
    )
    assert decision.protection_active
    exit_order = first.order_book.get(decision.order_id)
    assert exit_order.request.time_in_force is TimeInForce.GTC
    assert exit_order.request.order_type.value == order_type
    first.close()

    clock.value = datetime(2026, 9, 4, 4, 0, tzinfo=ET)
    restarted = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    try:
        restored = restarted.order_book.get(exit_order.order_id)
        assert not restored.is_terminal
        recovered = bridge(
            restarted,
            management_context_source=lambda _symbol: Signal().lifecycle_id,
        )
        recovered.begin_reconciliation()
        assert recovered.reconcile() is AutonomousPaperReadiness.READY
        assert (
            recovered.management_readiness("PMI")
            is AutonomousManagementReadiness.READY
        )
    finally:
        restarted.close()


def test_legacy_day_management_order_is_preserved_for_open_exposure() -> None:
    clock = Clock(START)
    composition = create_paper_trading_command_composition(clock=clock)
    execution, _ = submit_entry(composition)
    clock.value = START + timedelta(seconds=10)
    composition.gateway.process_market_event(quote(clock.value))
    decision = execution.ensure_exit(
        "PMI", 100, Decimal("9.50"), "STOP", Signal().lifecycle_id,
    )
    exit_order = composition.order_book.get(decision.order_id)
    legacy = replace(
        exit_order,
        request=replace(exit_order.request, time_in_force=TimeInForce.DAY),
    )
    composition.order_book.update(legacy)

    clock.value = datetime(2026, 9, 4, 4, 0, tzinfo=ET)
    assert composition.gateway.reconcile_temporal_validity(at=clock.value) == ()
    assert not composition.order_book.get(exit_order.order_id).is_terminal
    composition.close()


def test_expired_lifecycle_never_resubmits_but_new_authorization_can(tmp_path) -> None:
    path = tmp_path / "new-lifecycle.sqlite3"
    clock = Clock(START)
    first = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    _, old = submit_entry(first)
    first.close()

    clock.value = START + timedelta(minutes=1)
    restarted = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    execution = bridge(restarted)
    execution.begin_reconciliation()
    assert execution.reconcile() is AutonomousPaperReadiness.READY
    assert len(restarted.order_book.history()) == 1
    assert not execution.submit_entry(Signal(), 100, Decimal("50"))
    assert len(restarted.order_book.history()) == 1

    fresh = Signal(lifecycle_id=(
        "WARRIOR_MOMENTUM_V1|PMI|2026-09-03 14:01:00+00:00|"
        "HIGH_OF_DAY_BREAKOUT|10.05|9.55"
    ), entry_trigger=Decimal("10.05"), stop_price=Decimal("9.55"))
    assert execution.submit_entry(fresh, 100, Decimal("50"))
    assert len(restarted.order_book.history()) == 2
    assert restarted.order_book.get(old.order_id).status is OrderStatus.EXPIRED
    restarted.close()


def test_repeated_restart_is_expiration_idempotent(tmp_path) -> None:
    path = tmp_path / "idempotent.sqlite3"
    clock = Clock(START)
    first = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    submit_entry(first)
    first.close()

    clock.value = START + timedelta(minutes=1)
    second = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    assert len(second.durable_store.events()) == 3
    second.close()
    clock.value += timedelta(minutes=1)
    third = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    assert len(third.durable_store.events()) == 3
    assert third.order_book.history()[0].status is OrderStatus.EXPIRED
    third.close()


def test_legacy_warrior_snapshot_uses_one_minute_restart_fallback(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    clock = Clock(START)
    first = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    _, order = submit_entry(first)
    first.close()

    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute(
            "SELECT payload FROM orders WHERE order_id=?", (order.order_id,),
        ).fetchone()[0])
        payload.pop("terminal_reason", None)
        payload["request"].pop("entry_valid_until", None)
        connection.execute(
            "UPDATE orders SET payload=? WHERE order_id=?",
            (json.dumps(payload, sort_keys=True), order.order_id),
        )

    clock.value = START + timedelta(minutes=1)
    restarted = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    assert restarted.order_book.get(order.order_id).status is OrderStatus.EXPIRED
    assert (
        restarted.order_book.get(order.order_id).terminal_reason
        is OrderTerminalReason.ENTRY_STALE
    )
    restarted.close()


def test_ambiguous_expiry_commit_recovers_without_duplicate_event(tmp_path) -> None:
    path = tmp_path / "ambiguous-expiry.sqlite3"
    clock = Clock(START)
    first = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    submit_entry(first)
    first.close()

    delegate = DurablePaperExecutionStore(path, account_id=PAPER_ACCOUNT_ID)

    class CommitThenFail:
        def orders(self):
            return delegate.orders()

        def events(self):
            return delegate.events()

        def persist(self, event, order=None):
            delegate.persist(event, order)
            raise RuntimeError("injected crash after commit")

    clock.value = START + timedelta(minutes=1)
    with pytest.raises(PaperDurabilityError):
        PaperOrderGateway(
            PaperOrderBook(), durable_store=CommitThenFail(), clock=clock,
        )
    delegate.close()

    restarted = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    try:
        assert restarted.order_book.history()[0].status is OrderStatus.EXPIRED
        events = restarted.durable_store.events()
        assert [event.event_type for event in events].count("ORDER_EXPIRED") == 1
        assert [event.sequence for event in events] == [1, 2, 3]
    finally:
        restarted.close()


def test_gui_and_account_projection_treat_expiry_as_terminal(tmp_path) -> None:
    path = tmp_path / "projection.sqlite3"
    clock = Clock(START)
    first = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock,
    )
    submit_entry(first)
    first.close()

    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    pipeline = create_runtime_projection_pipeline(
        operations_bus=bus,
        account_id=PAPER_ACCOUNT_ID,
    )
    clock.value = START + timedelta(minutes=1)
    restarted = create_paper_trading_command_composition(
        persistence_path=str(path), clock=clock, event_sink=pipeline.sink,
    )
    try:
        state = store.snapshot()
        dashboard = project_dashboard(state)
        assert state.portfolio_projection.working_orders == 0
        assert state.orders[0].status == "EXPIRED"
        assert state.orders[0].execution_reason == "ENTRY_STALE"
        assert dashboard.orders.rows[0][-1] == "EXPIRED"
    finally:
        restarted.close()
        store.close()


def test_live_mode_remains_fail_closed_for_autonomous_paper_entry() -> None:
    composition = create_paper_trading_command_composition()
    execution = bridge(composition, mode="LIVE")
    assert not execution.submit_entry(Signal(), 100, Decimal("50"))
    assert composition.order_book.history() == ()
    composition.close()
