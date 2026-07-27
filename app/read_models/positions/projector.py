from __future__ import annotations

from app.operations_core import ApplicationState, OperationsPosition
from app.read_models.positions.models import (
    PositionReadModel,
    PositionsReadModelSnapshot,
)


def project_positions_read_model(
    state: ApplicationState,
) -> PositionsReadModelSnapshot:
    """Project authoritative application state into a positions read model."""

    if not isinstance(state, ApplicationState):
        raise TypeError("state must be an ApplicationState")

    return project_operational_positions(state.positions)


def project_operational_positions(
    positions: tuple[OperationsPosition, ...],
) -> PositionsReadModelSnapshot:
    """Project an immutable operational-position collection."""

    if not isinstance(positions, tuple):
        raise TypeError("positions must be an immutable tuple")

    if any(
        not isinstance(position, OperationsPosition)
        for position in positions
    ):
        raise TypeError(
            "positions must contain only OperationsPosition instances"
        )

    return PositionsReadModelSnapshot(
        positions=tuple(
            _project_position(position)
            for position in positions
        )
    )


def _project_position(
    position: OperationsPosition,
) -> PositionReadModel:
    return PositionReadModel(
        account_id=position.account_id,
        symbol=position.symbol,
        asset_type=position.asset_type,
        quantity=position.quantity,
        average_cost=position.average_cost,
        market_value=position.market_value,
        unrealized_gain_loss=position.unrealized_gain_loss,
        realized_gain_loss=position.realized_gain_loss,
        currency=position.currency,
        updated_at=position.updated_at,
    )
