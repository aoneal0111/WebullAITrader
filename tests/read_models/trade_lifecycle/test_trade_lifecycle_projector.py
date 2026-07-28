from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.operations_core import (
    DecisionsUpdated,
    OperationsBus,
    OperationsDecision,
    OperationsOrder,
    OperationsPosition,
    OrdersUpdated,
    PaperOrderLifecycleUpdated,
    PositionsUpdated,
    RuntimeCycleCompleted,
    RuntimeFailed,
    RuntimeStopped,
    TradeLifecycleUpdated,
)
from app.read_models.trade_lifecycle import (
    TradeLifecyclePhase,
    TradeLifecycleProjector,
    TradeLifecycleStatus,
)


NOW = datetime(2026, 7, 28, 17, 0, tzinfo=timezone.utc)


def decision(symbol: str, action: str = "ENTER_LONG") -> OperationsDecision:
    return OperationsDecision(
        symbol=symbol,
        action=action,
        confidence=85,
        score=Decimal("0.85"),
        reasons=("signal confirmed",),
        source_action="BUY",
        position_quantity=Decimal("0"),
        strategy_version="1.0",
        decided_at=NOW,
    )


def order(
    symbol: str,
    order_id: str,
    status: str,
) -> OperationsOrder:
    return OperationsOrder(
        order_id=order_id,
        symbol=symbol,
        side="BUY",
        quantity="1",
        status=status,
        updated_at=NOW,
    )


def position(
    symbol: str,
    *,
    quantity: str = "1",
    realized_pnl: str | None = None,
) -> OperationsPosition:
    return OperationsPosition(
        account_id="paper-account",
        symbol=symbol,
        asset_type="EQUITY",
        quantity=quantity,
        average_cost="100",
        market_value="105",
        unrealized_gain_loss="5",
        realized_gain_loss=realized_pnl,
        currency="USD",
        updated_at=NOW,
    )


def by_symbol(projector: TradeLifecycleProjector, symbol: str):
    return next(
        lifecycle
        for lifecycle in projector.snapshot().lifecycles
        if lifecycle.symbol == symbol
    )


def test_groups_entries_by_symbol_for_concurrent_trades() -> None:
    bus = OperationsBus()
    projector = TradeLifecycleProjector(bus)
    try:
        bus.publish(
            DecisionsUpdated(
                cycle=1,
                decisions=(decision("AAPL"), decision("MSFT")),
                occurred_at=NOW,
            )
        )
        bus.publish(
            OrdersUpdated(
                orders=(
                    order("AAPL", "aapl-1", "ACCEPTED"),
                    order("MSFT", "msft-1", "SUBMITTED"),
                ),
                occurred_at=NOW + timedelta(seconds=1),
            )
        )

        snapshot = projector.snapshot()
        assert tuple(item.symbol for item in snapshot.lifecycles) == (
            "AAPL",
            "MSFT",
        )
        assert all(
            item.status is TradeLifecycleStatus.OPEN
            for item in snapshot.lifecycles
        )
        assert tuple(
            entry.phase
            for entry in by_symbol(projector, "AAPL").entries
        ) == (
            TradeLifecyclePhase.DECISION,
            TradeLifecyclePhase.ORDER_ACCEPTED,
        )
        assert snapshot.selected_symbol == "MSFT"
    finally:
        projector.close()


def test_custom_phases_preserve_lifecycle_order() -> None:
    bus = OperationsBus()
    projector = TradeLifecycleProjector(bus)
    phases = (
        TradeLifecyclePhase.SCANNED,
        TradeLifecyclePhase.EVIDENCE,
        TradeLifecyclePhase.COMMITTEE,
        TradeLifecyclePhase.DECISION,
        TradeLifecyclePhase.STOP_UPDATED,
        TradeLifecyclePhase.TARGET_UPDATED,
    )
    try:
        for index, phase in enumerate(phases):
            bus.publish(
                TradeLifecycleUpdated(
                    symbol="AAPL",
                    phase=phase.value,
                    title=phase.value,
                    description=f"Recorded {phase.value}.",
                    cycle=1,
                    occurred_at=NOW + timedelta(seconds=index),
                )
            )

        lifecycle = by_symbol(projector, "AAPL")
        assert tuple(entry.phase for entry in lifecycle.entries) == phases
        assert lifecycle.opened_at == NOW
        assert lifecycle.status is TradeLifecycleStatus.OPEN
    finally:
        projector.close()


def test_order_lifecycle_correlates_by_prior_order_id() -> None:
    bus = OperationsBus()
    projector = TradeLifecycleProjector(bus)
    try:
        bus.publish(
            OrdersUpdated(
                orders=(order("AAPL", "order-1", "SUBMITTED"),),
                occurred_at=NOW,
            )
        )
        bus.publish(
            PaperOrderLifecycleUpdated(
                order_id="order-1",
                previous_status="ACCEPTED",
                current_status="FILLED",
                filled_quantity=Decimal("1"),
                remaining_quantity=Decimal("0"),
                fill_price=Decimal("100"),
                occurred_at=NOW + timedelta(seconds=1),
            )
        )

        lifecycle = by_symbol(projector, "AAPL")
        assert lifecycle.entries[-1].phase is TradeLifecyclePhase.FILLED
        assert lifecycle.entries[-1].order_id == "order-1"
    finally:
        projector.close()


def test_order_statuses_map_to_complete_fill_phase_sequence() -> None:
    bus = OperationsBus()
    projector = TradeLifecycleProjector(bus)
    statuses = (
        "SUBMITTED",
        "ACCEPTED",
        "PARTIALLY_FILLED",
        "FILLED",
    )
    try:
        for index, status in enumerate(statuses):
            bus.publish(
                OrdersUpdated(
                    orders=(order("AAPL", "order-1", status),),
                    occurred_at=NOW + timedelta(seconds=index),
                )
            )

        assert tuple(
            entry.phase
            for entry in by_symbol(projector, "AAPL").entries
        ) == (
            TradeLifecyclePhase.ORDER_SUBMITTED,
            TradeLifecyclePhase.ORDER_ACCEPTED,
            TradeLifecyclePhase.PARTIAL_FILL,
            TradeLifecyclePhase.FILLED,
        )
    finally:
        projector.close()


def test_positions_update_status_and_realized_pnl_then_close() -> None:
    bus = OperationsBus()
    projector = TradeLifecycleProjector(bus)
    try:
        bus.publish(
            PositionsUpdated(
                positions=(
                    position(
                        "AAPL",
                        realized_pnl="42.13",
                    ),
                ),
                occurred_at=NOW,
            )
        )
        opened = by_symbol(projector, "AAPL")
        assert opened.status is TradeLifecycleStatus.OPEN
        assert opened.realized_pnl == Decimal("42.13")
        assert opened.entries[-1].phase is (
            TradeLifecyclePhase.POSITION_OPEN
        )

        bus.publish(
            PositionsUpdated(
                positions=(),
                occurred_at=NOW + timedelta(minutes=1),
            )
        )
        closed = by_symbol(projector, "AAPL")
        assert closed.status is TradeLifecycleStatus.CLOSED
        assert closed.closed_at == NOW + timedelta(minutes=1)
        assert closed.realized_pnl == Decimal("42.13")
        assert closed.entries[-1].phase is (
            TradeLifecyclePhase.POSITION_CLOSE
        )
    finally:
        projector.close()


def test_runtime_cycle_appends_to_each_open_trade() -> None:
    bus = OperationsBus()
    projector = TradeLifecycleProjector(bus)
    try:
        bus.publish(
            DecisionsUpdated(
                cycle=1,
                decisions=(decision("AAPL"), decision("NVDA")),
                occurred_at=NOW,
            )
        )
        bus.publish(
            RuntimeCycleCompleted(
                cycle_count=1,
                occurred_at=NOW + timedelta(seconds=1),
            )
        )

        for symbol in ("AAPL", "NVDA"):
            entry = by_symbol(projector, symbol).entries[-1]
            assert entry.phase is TradeLifecyclePhase.RISK_UPDATE
            assert entry.cycle == 1
    finally:
        projector.close()


def test_runtime_stopped_closes_open_trades_and_failure_marks_failed() -> None:
    bus = OperationsBus()
    projector = TradeLifecycleProjector(bus)
    try:
        bus.publish(
            DecisionsUpdated(
                cycle=1,
                decisions=(decision("AAPL"), decision("MSFT")),
                occurred_at=NOW,
            )
        )
        bus.publish(
            RuntimeFailed(
                error_message="runtime error",
                occurred_at=NOW + timedelta(seconds=1),
            )
        )
        assert all(
            lifecycle.status is TradeLifecycleStatus.FAILED
            for lifecycle in projector.snapshot().lifecycles
        )

        bus.publish(
            TradeLifecycleUpdated(
                symbol="NVDA",
                phase="SCANNED",
                title="Scanned",
                description="Candidate selected.",
                occurred_at=NOW + timedelta(seconds=2),
            )
        )
        bus.publish(
            RuntimeStopped(
                cycles_completed=1,
                occurred_at=NOW + timedelta(seconds=3),
            )
        )
        assert by_symbol(projector, "NVDA").status is (
            TradeLifecycleStatus.CLOSED
        )
    finally:
        projector.close()


def test_hold_decision_remains_unknown_until_trade_activity() -> None:
    bus = OperationsBus()
    projector = TradeLifecycleProjector(bus)
    try:
        bus.publish(
            DecisionsUpdated(
                cycle=1,
                decisions=(decision("AAPL", "HOLD"),),
                occurred_at=NOW,
            )
        )

        lifecycle = by_symbol(projector, "AAPL")
        assert lifecycle.status is TradeLifecycleStatus.UNKNOWN
        assert lifecycle.opened_at is None
    finally:
        projector.close()


def test_close_unsubscribes_and_is_idempotent() -> None:
    bus = OperationsBus()
    projector = TradeLifecycleProjector(bus)

    assert bus.subscription_count == 1
    projector.close()
    projector.close()

    assert bus.subscription_count == 0
