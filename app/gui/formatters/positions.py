from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.gui.formatters.prices import format_price
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
                market_value=position.market_value,
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
    market_value: str | None,
    unrealized_gain_loss: str | None,
    currency: str,
) -> tuple[str, str, str, str]:
    quantity_value = _decimal(quantity, "quantity")
    quantity_label = _format_quantity(str(abs(quantity_value)))
    average_cost_label = _format_unit_price(average_cost, currency=currency)
    profit_loss_label = (
        _format_money(
            unrealized_gain_loss,
            currency=currency,
            include_sign=True,
        )
        if unrealized_gain_loss is not None
        else "--"
    )
    mark_label = "--"
    if market_value is not None and quantity_value != 0:
        mark_label = _format_unit_price(
            str(abs(_decimal(market_value, "market value") / quantity_value)),
            currency=currency,
        )
    pnl_percent = "--"
    if unrealized_gain_loss is not None:
        cost = abs(quantity_value * _decimal(average_cost, "average cost"))
        if cost:
            pnl_percent = f"{(_decimal(unrealized_gain_loss, 'profit/loss') / cost * Decimal('100')):+.2f}%"

    return (
        symbol,
        "LONG" if quantity_value > 0 else "SHORT" if quantity_value < 0 else "FLAT",
        quantity_label,
        average_cost_label,
        mark_label,
        profit_loss_label,
        pnl_percent,
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


def _format_unit_price(value: str, *, currency: str) -> str:
    currency_prefix = "$" if currency == "USD" else f"{currency} "
    return f"{currency_prefix}{format_price(_decimal(value, 'price'))}"


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
