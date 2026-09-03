from datetime import UTC, datetime, timedelta

from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    OperationsOrder,
    OperationsPosition,
    OrdersUpdated,
    PositionsUpdated,
    ProjectionAuthority,
)


NOW = datetime(2026, 9, 3, 21, 9, tzinfo=UTC)


def order(
    *,
    order_id: str = "chpt-entry",
    status: str = "WORKING",
    updated_at: datetime = NOW,
    remaining: str = "1833",
) -> OperationsOrder:
    return OperationsOrder(
        order_id=order_id,
        symbol="CHPT",
        side="BUY",
        quantity="1833",
        status=status,
        updated_at=updated_at,
        order_type="LIMIT",
        limit_price="9.104550",
        filled_quantity="0",
        remaining_quantity=remaining,
        lifecycle_id="WARRIOR_MOMENTUM_V1|CHPT|HIGH_OF_DAY_BREAKOUT",
        execution_reason="HIGH_OF_DAY_BREAKOUT",
        execution_source="paper-order-gateway",
    )


def position(*, symbol: str, quantity: str) -> OperationsPosition:
    return OperationsPosition(
        account_id="paper-account",
        symbol=symbol,
        asset_type="EQUITY",
        quantity=quantity,
        average_cost="4.5297" if symbol == "TLYS" else "9.10",
        market_value="0" if quantity == "0" else "18330",
        unrealized_gain_loss="0",
        realized_gain_loss="-517.71" if symbol == "TLYS" else "0",
        currency="USD",
        updated_at=NOW,
        exposure="0" if quantity == "0" else "18330",
    )


def publish_orders(bus, authority, orders):
    bus.publish(OrdersUpdated(
        source="test-projection",
        occurred_at=NOW,
        projection_authority=authority,
        orders=orders,
    ))


def publish_positions(bus, authority, positions):
    bus.publish(PositionsUpdated(
        source="test-projection",
        occurred_at=NOW,
        projection_authority=authority,
        positions=positions,
    ))


def test_paper_working_order_survives_broker_empty_in_both_orders() -> None:
    for authorities in (
        (ProjectionAuthority.PAPER_EXECUTION, ProjectionAuthority.BROKER_CURRENT),
        (ProjectionAuthority.BROKER_CURRENT, ProjectionAuthority.PAPER_EXECUTION),
    ):
        bus = OperationsBus()
        store = ApplicationStateStore(bus)
        try:
            for authority in authorities:
                publish_orders(
                    bus,
                    authority,
                    (order(),) if authority is ProjectionAuthority.PAPER_EXECUTION else (),
                )
            state = store.snapshot()
            assert state.paper_orders == (order(),)
            assert state.broker_orders == ()
            assert state.orders == (order(),)
            assert state.order_projection.orders[0].status == "WORKING"
            assert state.order_projection.orders[0].remaining_quantity == "1833"
            assert state.portfolio_projection.working_orders == 1
        finally:
            store.close()


def test_unrelated_market_position_update_cannot_hide_paper_order() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    try:
        publish_orders(bus, ProjectionAuthority.PAPER_EXECUTION, (order(),))
        publish_orders(bus, ProjectionAuthority.BROKER_CURRENT, ())
        publish_positions(
            bus,
            ProjectionAuthority.PAPER_EXECUTION,
            (position(symbol="TLYS", quantity="0"),),
        )
        assert store.snapshot().orders == (order(),)
    finally:
        store.close()


def test_broker_empty_preserves_flat_history_without_making_it_current() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    try:
        closed = position(symbol="TLYS", quantity="0")
        publish_positions(bus, ProjectionAuthority.PAPER_EXECUTION, (closed,))
        publish_positions(bus, ProjectionAuthority.BROKER_CURRENT, ())
        state = store.snapshot()
        assert state.paper_positions == (closed,)
        assert state.positions == (closed,)
    finally:
        store.close()


def test_broker_empty_does_not_erase_paper_current_exposure() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    try:
        opened = position(symbol="CHPT", quantity="1833")
        publish_positions(bus, ProjectionAuthority.PAPER_EXECUTION, (opened,))
        publish_positions(bus, ProjectionAuthority.BROKER_CURRENT, ())
        assert store.snapshot().positions == (opened,)
    finally:
        store.close()


def test_cancelled_positive_remaining_is_not_prioritized_as_working() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    try:
        cancelled = order(status="CANCELLED")
        working = order(order_id="other-working")
        publish_orders(
            bus,
            ProjectionAuthority.PAPER_EXECUTION,
            (cancelled, working),
        )
        assert tuple(item.order_id for item in store.snapshot().orders) == (
            "other-working", "chpt-entry",
        )
    finally:
        store.close()


def test_duplicate_order_merges_once_and_terminal_dominates_obsolete_working() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    try:
        publish_orders(
            bus,
            ProjectionAuthority.BROKER_CURRENT,
            (order(updated_at=NOW + timedelta(minutes=1)),),
        )
        terminal = order(status="CANCELLED", updated_at=NOW)
        publish_orders(
            bus,
            ProjectionAuthority.PAPER_EXECUTION,
            (terminal,),
        )
        assert store.snapshot().orders == (terminal,)
    finally:
        store.close()


def test_terminal_paper_update_moves_order_out_of_working_state() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    try:
        publish_orders(bus, ProjectionAuthority.PAPER_EXECUTION, (order(),))
        filled = order(
            status="FILLED",
            updated_at=NOW + timedelta(minutes=1),
            remaining="0",
        )
        publish_orders(bus, ProjectionAuthority.PAPER_EXECUTION, (filled,))
        assert store.snapshot().orders == (filled,)
    finally:
        store.close()


def test_source_replay_permutations_have_identical_canonical_result() -> None:
    terminal = order(status="CANCELLED", updated_at=NOW, remaining="1833")
    broker_working = order(updated_at=NOW + timedelta(minutes=1))
    results = []
    for updates in (
        (
            (ProjectionAuthority.PAPER_EXECUTION, (terminal,)),
            (ProjectionAuthority.BROKER_CURRENT, (broker_working,)),
        ),
        (
            (ProjectionAuthority.BROKER_CURRENT, (broker_working,)),
            (ProjectionAuthority.PAPER_EXECUTION, (terminal,)),
        ),
    ):
        bus = OperationsBus()
        store = ApplicationStateStore(bus)
        try:
            for authority, orders in updates:
                publish_orders(bus, authority, orders)
            results.append(store.snapshot().order_projection)
        finally:
            store.close()
    assert results[0] == results[1]
    assert results[0].orders[0].status == "CANCELLED"
