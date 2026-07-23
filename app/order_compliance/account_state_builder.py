from __future__ import annotations

from decimal import Decimal

from app.order_compliance.models import AccountComplianceState


def build_account_state(
    *,
    portfolio,
    account_type,
    filled_orders: int,
    symbol: str,
    timestamp,
) -> AccountComplianceState:
    """
    Build an AccountComplianceState from the current paper portfolio.

    This contains no business logic. It simply adapts the current
    portfolio/session into the compliance model.
    """
    normalized_symbol = symbol.strip().upper()

    position = next(
        (
            item
            for item in portfolio.positions
            if item.symbol == normalized_symbol
        ),
        None,
    )

    return AccountComplianceState(
        account_type,
        portfolio.equity,
        portfolio.realized_pnl,
        portfolio.unrealized_pnl,
        filled_orders,
        (),
        (),
        position.quantity if position else Decimal(0),
        position.market_value if position else Decimal(0),
        sum(
            (
                item.market_value
                for item in portfolio.positions
            ),
            Decimal(0),
        ),
        timestamp,
    )
