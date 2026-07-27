from datetime import datetime, timezone

from app.operations_core import (
    ApplicationStateStore,
    OperationsBus,
    OperationsPosition,
    PositionsUpdated,
    RuntimeStarted,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def make_position(
    *,
    account_id: str = "account-1",
    symbol: str = "AAPL",
    quantity: str = "10",
) -> OperationsPosition:
    return OperationsPosition(
        account_id=account_id,
        symbol=symbol,
        asset_type="EQUITY",
        quantity=quantity,
        average_cost="185.25",
        market_value="1900.00",
        unrealized_gain_loss="47.50",
        realized_gain_loss=None,
        currency="USD",
        updated_at=NOW,
    )


def test_initial_application_state_has_no_positions() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    try:
        snapshot = store.snapshot()

        assert snapshot.positions == ()
        assert snapshot.revision == 0
    finally:
        store.close()


def test_positions_updated_replaces_the_position_slice() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    first = make_position()
    second = make_position(
        account_id="account-2",
        symbol="MSFT",
        quantity="5",
    )

    try:
        bus.publish(
            PositionsUpdated(
                source="positions-runtime",
                positions=(first, second),
                occurred_at=NOW,
            )
        )

        snapshot = store.snapshot()

        assert snapshot.positions == (first, second)
        assert snapshot.revision == 1
        assert snapshot.timeline[-1].event_type == "PositionsUpdated"
        assert snapshot.timeline[-1].source == "positions-runtime"
        assert snapshot.timeline[-1].message == (
            "Position state updated: 2 positions."
        )
    finally:
        store.close()


def test_positions_updated_uses_singular_timeline_message() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    try:
        bus.publish(
            PositionsUpdated(
                positions=(make_position(),),
                occurred_at=NOW,
            )
        )

        assert store.snapshot().timeline[-1].message == (
            "Position state updated: 1 position."
        )
    finally:
        store.close()


def test_empty_positions_updated_clears_the_position_slice() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)

    try:
        bus.publish(
            PositionsUpdated(
                positions=(make_position(),),
                occurred_at=NOW,
            )
        )
        bus.publish(
            PositionsUpdated(
                positions=(),
                occurred_at=NOW,
            )
        )

        snapshot = store.snapshot()

        assert snapshot.positions == ()
        assert snapshot.revision == 2
        assert snapshot.timeline[-1].message == (
            "Position state updated: 0 positions."
        )
    finally:
        store.close()


def test_unrelated_events_preserve_existing_positions() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    position = make_position()

    try:
        bus.publish(
            PositionsUpdated(
                positions=(position,),
                occurred_at=NOW,
            )
        )
        bus.publish(
            RuntimeStarted(
                environment="PAPER",
                active_model="Atlas Test Model",
                occurred_at=NOW,
            )
        )

        snapshot = store.snapshot()

        assert snapshot.positions == (position,)
        assert snapshot.runtime.active_model == "Atlas Test Model"
        assert snapshot.revision == 2
    finally:
        store.close()
