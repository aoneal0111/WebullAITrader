from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.broker_protocol.models import BrokerPosition
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


def map_broker_positions(
    positions: tuple[BrokerPosition, ...],
    *,
    account_id: str,
    currency: str,
    updated_at: datetime,
) -> tuple[OperationsPosition, ...]:
    """Map live broker positions into backend-neutral operations state.

    BrokerPosition does not expose unrealized P/L directly. It is therefore
    derived from broker-provided market value and cost basis. When market value
    is unavailable, cost basis is used as the neutral fallback, producing zero
    unrealized P/L without inventing a market price.
    """
    if not isinstance(positions, tuple):
        raise TypeError("positions must be an immutable tuple")
    if any(not isinstance(position, BrokerPosition) for position in positions):
        raise TypeError(
            "positions must contain only BrokerPosition instances"
        )
    if not isinstance(account_id, str) or not account_id.strip():
        raise ValueError("account_id must not be empty")
    if account_id != account_id.strip():
        raise ValueError("account_id must be stripped")
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError("currency must not be empty")
    if currency != currency.strip():
        raise ValueError("currency must be stripped")
    if not isinstance(updated_at, datetime):
        raise TypeError("updated_at must be a datetime")
    if updated_at.tzinfo is None:
        raise ValueError("updated_at must be timezone-aware")

    normalized_currency = currency.upper()

    return tuple(
        _map_broker_position(
            position,
            account_id=account_id,
            currency=normalized_currency,
            updated_at=updated_at,
        )
        for position in sorted(
            positions,
            key=lambda item: item.symbol,
        )
    )


def _map_broker_position(
    position: BrokerPosition,
    *,
    account_id: str,
    currency: str,
    updated_at: datetime,
) -> OperationsPosition:
    cost_basis = position.quantity * position.average_price
    market_value = (
        position.market_value
        if position.market_value is not None
        else cost_basis
    )
    unrealized_gain_loss = market_value - cost_basis

    return OperationsPosition(
        account_id=account_id,
        symbol=position.symbol.strip().upper(),
        asset_type="EQUITY",
        quantity=_decimal_text(position.quantity),
        average_cost=_decimal_text(position.average_price),
        market_value=_decimal_text(market_value),
        unrealized_gain_loss=_decimal_text(unrealized_gain_loss),
        realized_gain_loss=None,
        currency=currency,
        updated_at=updated_at,
    )


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


__all__ = [
    "map_broker_positions",
    "map_paper_positions",
]
