from datetime import datetime, timezone

import pytest

from app.gui.formatters import format_positions
from app.gui.models import PositionsSnapshot
from app.read_models.positions import (
    PositionReadModel,
    PositionsReadModelSnapshot,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def make_read_model_position(
    *,
    account_id: str = "account-1",
    symbol: str = "AAPL",
    asset_type: str = "EQUITY",
    quantity: str = "10",
    average_cost: str = "185.25",
    market_value: str = "1900.00",
    unrealized_gain_loss: str = "47.50",
    realized_gain_loss: str | None = None,
    currency: str = "USD",
) -> PositionReadModel:
    return PositionReadModel(
        account_id=account_id,
        symbol=symbol,
        asset_type=asset_type,
        quantity=quantity,
        average_cost=average_cost,
        market_value=market_value,
        unrealized_gain_loss=unrealized_gain_loss,
        realized_gain_loss=realized_gain_loss,
        currency=currency,
        updated_at=NOW,
    )


def test_format_positions_returns_empty_snapshot() -> None:
    snapshot = format_positions(
        PositionsReadModelSnapshot.initial()
    )

    assert snapshot == PositionsSnapshot.initial()


def test_format_positions_creates_dashboard_rows() -> None:
    first = make_read_model_position()
    second = make_read_model_position(
        account_id="account-2",
        symbol="MSFT",
        quantity="5",
        average_cost="410",
        market_value="2025",
        unrealized_gain_loss="-25",
    )

    snapshot = format_positions(
        PositionsReadModelSnapshot(
            positions=(first, second),
        )
    )

    assert snapshot.rows == (
        (
            "AAPL",
            "10",
            "$185.25",
            "+$47.50",
        ),
        (
            "MSFT",
            "5",
            "$410.00",
            "-$25.00",
        ),
    )


def test_format_positions_preserves_fractional_quantity() -> None:
    snapshot = format_positions(
        PositionsReadModelSnapshot(
            positions=(
                make_read_model_position(
                    quantity="10.5000",
                ),
            ),
        )
    )

    assert snapshot.rows[0][1] == "10.5"


def test_format_positions_formats_zero_profit_loss_without_sign() -> None:
    snapshot = format_positions(
        PositionsReadModelSnapshot(
            positions=(
                make_read_model_position(
                    unrealized_gain_loss="0",
                ),
            ),
        )
    )

    assert snapshot.rows[0][3] == "$0.00"


def test_format_positions_formats_non_usd_currency() -> None:
    snapshot = format_positions(
        PositionsReadModelSnapshot(
            positions=(
                make_read_model_position(
                    currency="EUR",
                    average_cost="1000.5",
                    unrealized_gain_loss="25",
                ),
            ),
        )
    )

    assert snapshot.rows[0][2] == "EUR 1,000.50"
    assert snapshot.rows[0][3] == "+EUR 25.00"


def test_format_positions_preserves_source_ordering() -> None:
    snapshot = format_positions(
        PositionsReadModelSnapshot(
            positions=(
                make_read_model_position(symbol="AAPL"),
                make_read_model_position(
                    account_id="account-2",
                    symbol="MSFT",
                ),
            ),
        )
    )

    assert tuple(row[0] for row in snapshot.rows) == (
        "AAPL",
        "MSFT",
    )


def test_format_positions_returns_immutable_rows() -> None:
    snapshot = format_positions(
        PositionsReadModelSnapshot(
            positions=(make_read_model_position(),),
        )
    )

    assert isinstance(snapshot.rows, tuple)
    assert isinstance(snapshot.rows[0], tuple)


def test_format_positions_rejects_wrong_snapshot_type() -> None:
    with pytest.raises(
        TypeError,
        match="snapshot must be a PositionsReadModelSnapshot",
    ):
        format_positions(object())  # type: ignore[arg-type]
