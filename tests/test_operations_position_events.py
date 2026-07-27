from datetime import datetime, timezone

import pytest

from app.operations_core import OperationsPosition, PositionsUpdated


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def make_position() -> OperationsPosition:
    return OperationsPosition(
        account_id="account-1",
        symbol="AAPL",
        asset_type="EQUITY",
        quantity="10",
        average_cost="185.25",
        market_value="1900.00",
        unrealized_gain_loss="47.50",
        realized_gain_loss=None,
        currency="USD",
        updated_at=NOW,
    )


def test_operations_position_accepts_valid_contract() -> None:
    position = make_position()

    assert position.account_id == "account-1"
    assert position.symbol == "AAPL"
    assert position.asset_type == "EQUITY"
    assert position.quantity == "10"
    assert position.average_cost == "185.25"
    assert position.market_value == "1900.00"
    assert position.unrealized_gain_loss == "47.50"
    assert position.realized_gain_loss is None
    assert position.currency == "USD"
    assert position.updated_at == NOW


@pytest.mark.parametrize(
    "field_name",
    (
        "account_id",
        "symbol",
        "asset_type",
        "quantity",
        "average_cost",
        "market_value",
        "unrealized_gain_loss",
        "currency",
    ),
)
def test_operations_position_rejects_blank_required_text(
    field_name: str,
) -> None:
    values = {
        "account_id": "account-1",
        "symbol": "AAPL",
        "asset_type": "EQUITY",
        "quantity": "10",
        "average_cost": "185.25",
        "market_value": "1900.00",
        "unrealized_gain_loss": "47.50",
        "realized_gain_loss": None,
        "currency": "USD",
        "updated_at": NOW,
    }
    values[field_name] = " "

    with pytest.raises(ValueError):
        OperationsPosition(**values)


def test_operations_position_accepts_realized_gain_loss() -> None:
    position = OperationsPosition(
        account_id="account-1",
        symbol="AAPL",
        asset_type="EQUITY",
        quantity="10",
        average_cost="185.25",
        market_value="1900.00",
        unrealized_gain_loss="47.50",
        realized_gain_loss="12.25",
        currency="USD",
        updated_at=NOW,
    )

    assert position.realized_gain_loss == "12.25"


def test_operations_position_rejects_blank_realized_gain_loss() -> None:
    with pytest.raises(ValueError, match="realized_gain_loss"):
        OperationsPosition(
            account_id="account-1",
            symbol="AAPL",
            asset_type="EQUITY",
            quantity="10",
            average_cost="185.25",
            market_value="1900.00",
            unrealized_gain_loss="47.50",
            realized_gain_loss=" ",
            currency="USD",
            updated_at=NOW,
        )


def test_operations_position_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        OperationsPosition(
            account_id="account-1",
            symbol="AAPL",
            asset_type="EQUITY",
            quantity="10",
            average_cost="185.25",
            market_value="1900.00",
            unrealized_gain_loss="47.50",
            realized_gain_loss=None,
            currency="USD",
            updated_at=datetime(2026, 7, 27, 12, 0),
        )


def test_positions_updated_accepts_immutable_tuple() -> None:
    position = make_position()

    event = PositionsUpdated(
        source="test-position-source",
        positions=(position,),
        occurred_at=NOW,
    )

    assert event.positions == (position,)


def test_positions_updated_rejects_mutable_sequence() -> None:
    with pytest.raises(TypeError, match="immutable tuple"):
        PositionsUpdated(
            positions=[make_position()],
            occurred_at=NOW,
        )


def test_positions_updated_rejects_invalid_tuple_members() -> None:
    with pytest.raises(
        TypeError,
        match="OperationsPosition instances",
    ):
        PositionsUpdated(
            positions=("not-a-position",),
            occurred_at=NOW,
        )
