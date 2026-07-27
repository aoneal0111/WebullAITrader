from datetime import datetime, timezone

import pytest

from app.operations_core import (
    ApplicationState,
    OperationsPosition,
)
from app.read_models.positions.models import (
    PositionReadModel,
    PositionsReadModelSnapshot,
)
from app.read_models.positions.projector import (
    project_operational_positions,
    project_positions_read_model,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def make_operations_position(
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
) -> OperationsPosition:
    return OperationsPosition(
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


def test_empty_application_state_projects_initial_snapshot() -> None:
    snapshot = project_positions_read_model(ApplicationState())

    assert snapshot == PositionsReadModelSnapshot.initial()


def test_projector_preserves_operational_position_facts() -> None:
    source = make_operations_position(
        realized_gain_loss="12.25",
    )
    state = ApplicationState(positions=(source,))

    snapshot = project_positions_read_model(state)

    assert snapshot.positions == (
        PositionReadModel(
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
        ),
    )


def test_projector_preserves_source_ordering() -> None:
    first = make_operations_position(
        account_id="account-1",
        symbol="AAPL",
    )
    second = make_operations_position(
        account_id="account-2",
        symbol="MSFT",
        quantity="5",
        average_cost="410.00",
        market_value="2075.00",
        unrealized_gain_loss="25.00",
    )

    snapshot = project_operational_positions((first, second))

    assert tuple(
        position.symbol
        for position in snapshot.positions
    ) == ("AAPL", "MSFT")


def test_projection_returns_immutable_tuple() -> None:
    snapshot = project_operational_positions(
        (make_operations_position(),)
    )

    assert isinstance(snapshot.positions, tuple)


def test_projection_creates_read_model_instances() -> None:
    snapshot = project_operational_positions(
        (make_operations_position(),)
    )

    assert isinstance(snapshot.positions[0], PositionReadModel)


def test_projector_rejects_non_application_state() -> None:
    with pytest.raises(
        TypeError,
        match="state must be an ApplicationState",
    ):
        project_positions_read_model(object())  # type: ignore[arg-type]


def test_operational_projection_requires_tuple() -> None:
    with pytest.raises(
        TypeError,
        match="positions must be an immutable tuple",
    ):
        project_operational_positions(  # type: ignore[arg-type]
            [make_operations_position()]
        )


def test_operational_projection_rejects_invalid_members() -> None:
    with pytest.raises(
        TypeError,
        match="OperationsPosition instances",
    ):
        project_operational_positions(
            ("not-a-position",)  # type: ignore[arg-type]
        )
