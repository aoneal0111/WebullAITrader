from __future__ import annotations

from app.operations import PaperRuntimeCycleResult
from app.operations_core import OperationsPosition


def map_paper_positions(
    result: PaperRuntimeCycleResult,
) -> tuple[OperationsPosition, ...]:
    """Map paper portfolio positions into backend-neutral operations state."""
    if not isinstance(result, PaperRuntimeCycleResult):
        raise TypeError(
            "result must be a PaperRuntimeCycleResult"
        )

    portfolio = result.session.portfolio

    return tuple(
        OperationsPosition(
            account_id=result.session.session_id,
            symbol=position.symbol,
            asset_type="EQUITY",
            quantity=format(position.quantity, "f"),
            average_cost=format(position.average_cost, "f"),
            market_value=format(position.market_value, "f"),
            unrealized_gain_loss=format(
                position.unrealized_pnl,
                "f",
            ),
            realized_gain_loss=None,
            currency="USD",
            updated_at=portfolio.timestamp,
        )
        for position in sorted(
            portfolio.positions,
            key=lambda item: item.symbol,
        )
    )


__all__ = [
    "map_paper_positions",
]
