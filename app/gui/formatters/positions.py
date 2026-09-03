from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.gui.formatters.prices import format_price
from app.gui.models import PositionsSnapshot
from app.gui.models import PositionManagementRow, ProtectionSnapshot
from app.gui.formatters.orders import has_explicit_protection_evidence
from app.read_models.orders import OrderReadModel, OrdersReadModelSnapshot
from app.read_models.positions import PositionsReadModelSnapshot


def format_positions(
    snapshot: PositionsReadModelSnapshot,
    orders: OrdersReadModelSnapshot | None = None,
) -> PositionsSnapshot:
    """Format a positions read model for the dashboard positions panel."""

    if not isinstance(snapshot, PositionsReadModelSnapshot):
        raise TypeError(
            "snapshot must be a PositionsReadModelSnapshot"
        )

    rows = tuple(
            _format_position(
                symbol=position.symbol,
                quantity=position.quantity,
                average_cost=position.average_cost,
                market_value=position.market_value,
                unrealized_gain_loss=position.unrealized_gain_loss,
                realized_gain_loss=position.realized_gain_loss,
                currency=position.currency,
                updated_at=position.updated_at,
            )
            for position in snapshot.positions
        )
    return PositionsSnapshot(
        rows=rows,
        management=tuple(
            _management_row(position, row, orders)
            for position, row in zip(snapshot.positions, rows)
        ),
    )


def _management_row(position, row, orders) -> PositionManagementRow:
    protection = _correlated_protection(position, orders)
    strategy, setup = _strategy_setup(protection, orders)
    return PositionManagementRow(
        symbol=row[0], side=row[1], quantity=row[2], average_entry=row[3],
        mark=row[4], unrealized_pnl=row[5], unrealized_percent=row[6],
        realized_pnl=row[7], updated_at=row[8], strategy=strategy, setup=setup,
        management_state=(
            protection.status.replace("_", " ").title()
            if protection is not None else "Protection not evidenced"
        ),
        protection=protection,
    )


def _correlated_protection(position, orders) -> ProtectionSnapshot | None:
    if orders is None:
        return None
    quantity = _decimal(position.quantity, "quantity")
    expected_sides = {"SELL"} if quantity > 0 else {"BUY", "COVER"}
    active = {
        "NEW", "PENDING", "SUBMITTED", "ACCEPTED", "WORKING",
        "PARTIALLY_FILLED",
    }
    symbol = position.symbol.strip().upper()
    candidates: list[OrderReadModel] = []
    for order in orders.orders:
        lifecycle_tokens = {
            token.strip().upper()
            for token in (order.lifecycle_id or "").replace(":", "|").split("|")
        }
        if (
            has_explicit_protection_evidence(order)
            and order.symbol.strip().upper() == symbol
            and order.side.upper() in expected_sides
            and order.status.upper() in active
            and symbol in lifecycle_tokens
            and order.remaining_quantity is not None
            and _decimal(order.remaining_quantity, "remaining quantity") > 0
        ):
            candidates.append(order)
    if not candidates:
        return None
    order = max(candidates, key=lambda item: (item.updated_at, item.order_id))
    return ProtectionSnapshot(
        status=order.status,
        side=order.side,
        order_type=order.order_type or "STOP",
        remaining_quantity=_format_quantity(order.remaining_quantity or "0"),
        stop_price=format_price(_decimal(order.stop_price or "0", "stop price")),
        order_id=order.order_id,
    )


def _strategy_setup(protection, orders) -> tuple[str, str]:
    if protection is None or orders is None:
        return "—", "—"
    order = next(
        (item for item in orders.orders if item.order_id == protection.order_id), None
    )
    tokens = (order.lifecycle_id or "").split("|") if order is not None else []
    strategy = tokens[0].replace("_", " ").title() if len(tokens) >= 1 else "—"
    setup = tokens[2].replace("_", " ").title() if len(tokens) >= 3 else "—"
    return strategy, setup


def _format_position(
    *,
    symbol: str,
    quantity: str,
    average_cost: str,
    market_value: str | None,
    unrealized_gain_loss: str | None,
    realized_gain_loss: str | None,
    currency: str,
    updated_at,
) -> tuple[str, ...]:
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

    realized_label = (
        _format_money(realized_gain_loss, currency=currency, include_sign=True)
        if realized_gain_loss is not None
        else "—"
    )

    return (
        symbol,
        "LONG" if quantity_value > 0 else "SHORT" if quantity_value < 0 else "FLAT",
        quantity_label,
        average_cost_label,
        mark_label,
        profit_loss_label,
        pnl_percent,
        realized_label,
        updated_at.astimezone().strftime("%H:%M:%S"),
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
