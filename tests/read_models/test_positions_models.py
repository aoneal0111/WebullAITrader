from datetime import datetime, timezone

import pytest

from app.read_models.positions.models import (
    PositionReadModel,
    PositionsReadModelSnapshot,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def make_position() -> PositionReadModel:
    return PositionReadModel(
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


def test_position_read_model_is_immutable() -> None:
    position = make_position()

    with pytest.raises(AttributeError):
        position.quantity = "20"  # type: ignore[misc]


def test_position_read_model_preserves_all_fields() -> None:
    position = PositionReadModel(
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

    assert position.account_id == "account-1"
    assert position.symbol == "AAPL"
    assert position.asset_type == "EQUITY"
    assert position.quantity == "10"
    assert position.average_cost == "185.25"
    assert position.market_value == "1900.00"
    assert position.unrealized_gain_loss == "47.50"
    assert position.realized_gain_loss == "12.25"
    assert position.currency == "USD"
    assert position.updated_at == NOW


def test_position_read_model_requires_datetime() -> None:
    with pytest.raises(
        TypeError,
        match="updated_at must be a datetime",
    ):
        PositionReadModel(
            account_id="account-1",
            symbol="AAPL",
            asset_type="EQUITY",
            quantity="10",
            average_cost="185.25",
            market_value="1900.00",
            unrealized_gain_loss="47.50",
            realized_gain_loss=None,
            currency="USD",
            updated_at="not-a-datetime",  # type: ignore[arg-type]
        )


def test_position_read_model_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(
        ValueError,
        match="updated_at must be timezone-aware",
    ):
        PositionReadModel(
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
def test_position_read_model_rejects_blank_required_text(
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

    with pytest.raises(
        ValueError,
        match=f"{field_name} must not be empty",
    ):
        PositionReadModel(**values)


def test_position_read_model_allows_missing_realized_gain_loss() -> None:
    assert make_position().realized_gain_loss is None


def test_position_read_model_rejects_blank_realized_gain_loss() -> None:
    with pytest.raises(
        ValueError,
        match="realized_gain_loss must not be empty",
    ):
        PositionReadModel(
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


def test_initial_positions_snapshot_is_empty() -> None:
    assert PositionsReadModelSnapshot.initial().positions == ()


def test_positions_snapshot_requires_immutable_tuple() -> None:
    with pytest.raises(
        TypeError,
        match="positions must be an immutable tuple",
    ):
        PositionsReadModelSnapshot(
            positions=[make_position()],  # type: ignore[arg-type]
        )


def test_positions_snapshot_rejects_invalid_members() -> None:
    with pytest.raises(
        TypeError,
        match="PositionReadModel instances",
    ):
        PositionsReadModelSnapshot(
            positions=("not-a-position",),  # type: ignore[arg-type]
        )
