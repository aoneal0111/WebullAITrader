from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.gui.models import PositionsSnapshot
from app.read_models.positions import PositionsReadModelSnapshot


def format_positions(
    snapshot: PositionsReadModelSnapshot,
) -> PositionsSnapshot:
    """Format a positions read model for the dashboard positions panel."""

    if not isinstance(snapshot, PositionsReadModelSnapshot):
        raise TypeError(
            "snapshot must be a PositionsReadModelSnapshot"
        )

    return PositionsSnapshot(
        rows=tuple(
            _format_position(
                symbol=position.symbol,
                quantity=position.quantity,
                average_cost=position.average_cost,
                unrealized_gain_loss=position.unrealized_gain_loss,
                currency=position.currency,
            )
            for position in snapshot.positions
        )
    )


def _format_position(
    *,
    symbol: str,
    quantity: str,
    average_cost: str,
    unrealized_gain_loss: str | None,
    currency: str,
) -> tuple[str, str, str, str]:
    quantity_label = _format_quantity(quantity)
    average_cost_label = _format_money(
        average_cost,
        currency=currency,
        include_sign=False,
    )
    profit_loss_label = (
        _format_money(
            unrealized_gain_loss,
            currency=currency,
            include_sign=True,
        )
        if unrealized_gain_loss is not None
        else "--"
    )

    return (
        symbol,
        quantity_label,
        average_cost_label,
        profit_loss_label,
    )


def _format_quantity(value: str) -> str:
    decimal_value = _decimal(value, "quantity")

    if decimal_value == decimal_value.to_integral_value():
        return format(decimal_value, "f").split(".", 1)[0]

    return format(decimal_value.normalize(), "f")


def _format_money(
    value: str,
    *,
    currency: str,
    include_sign: bool,
) -> str:
    decimal_value = _decimal(value, "money value")
    currency_prefix = "$" if currency == "USD" else f"{currency} "

    if include_sign:
        if decimal_value > 0:
            sign = "+"
        elif decimal_value < 0:
            sign = "-"
        else:
            sign = ""

        return (
            f"{sign}{currency_prefix}"
            f"{abs(decimal_value):,.2f}"
        )

    return f"{currency_prefix}{decimal_value:,.2f}"


def _decimal(value: str, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be Decimal-compatible"
        ) from exc

    if not decimal_value.is_finite():
        raise ValueError(f"{field_name} must be finite")

    return decimal_value
