"""Session and point-in-time validity rules for PAPER orders.

Atlas treats a DAY as the NYSE trading date's supported extended-hours cycle:
04:00 through 20:00 America/New_York.  The repository calendar determines
whether the order's Eastern date is a trading day.  An order created on a
weekend or known holiday has no valid DAY and expires conservatively.

Autonomous Warrior entries are one-minute-bar hypotheses.  Their unfilled
authority lasts for one established Warrior bar interval from authorization.
This bound is independent of position validity: fills remain authoritative,
and long-position exits are preserved.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from app.market.calendar import EASTERN, trading_day_schedule
from app.paper_trading.order_models import (
    OrderSide,
    OrderStatus,
    OrderTerminalReason,
    OrderType,
    PaperOrder,
    TimeInForce,
)


ATLAS_DAY_START = time(4, 0)
ATLAS_DAY_END = time(20, 0)
WARRIOR_LIFECYCLE_PREFIX = "WARRIOR_MOMENTUM_V1|"
LEGACY_WARRIOR_ENTRY_VALIDITY = timedelta(minutes=1)


def atlas_day_expiration(order: PaperOrder) -> datetime:
    """Return the deterministic Eastern extended-hours DAY boundary."""

    created_eastern = order.created_at.astimezone(EASTERN)
    schedule = trading_day_schedule(created_eastern)
    if (
        schedule is None
        or created_eastern.time() < ATLAS_DAY_START
        or created_eastern.time() >= ATLAS_DAY_END
    ):
        return order.created_at
    return datetime.combine(
        schedule.trading_date,
        ATLAS_DAY_END,
        tzinfo=EASTERN,
    )


def is_autonomous_warrior_entry(order: PaperOrder) -> bool:
    """Classify only supported long autonomous entry orders."""

    lifecycle = order.request.strategy_lifecycle_id or ""
    return (
        order.side is OrderSide.BUY
        and order.request.order_type is OrderType.LIMIT
        and (
            order.request.entry_valid_until is not None
            or lifecycle.startswith(WARRIOR_LIFECYCLE_PREFIX)
        )
    )


def entry_valid_until(order: PaperOrder) -> datetime | None:
    """Resolve persisted validity, with a deterministic legacy fallback."""

    if not is_autonomous_warrior_entry(order):
        return None
    # Pre-milestone orders did not persist the explicit boundary.  Warrior's
    # established feature interval was one minute, so legacy recovery uses
    # that same authorization-time horizon rather than an unbounded order.
    return (
        order.request.entry_valid_until
        or order.created_at + LEGACY_WARRIOR_ENTRY_VALIDITY
    )


def temporal_terminal_reason(
    order: PaperOrder,
    *,
    evaluated_at: datetime,
    long_position_quantity: Decimal,
) -> OrderTerminalReason | None:
    """Return the terminal reason due before matching, if any.

    Partially filled BUY entries remain entries, so only their remainder
    expires.  A SELL backed by long exposure is management and is never
    removed by DAY rollover, including legacy DAY stops and targets.
    """

    if order.status not in {
        OrderStatus.NEW,
        OrderStatus.ACCEPTED,
        OrderStatus.PARTIALLY_FILLED,
    }:
        return None
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")

    preserves_long_management = (
        order.side is OrderSide.SELL
        and long_position_quantity > 0
    )
    if (
        order.request.time_in_force is TimeInForce.DAY
        and not preserves_long_management
        and evaluated_at >= atlas_day_expiration(order)
    ):
        return OrderTerminalReason.DAY_EXPIRED

    valid_until = entry_valid_until(order)
    if valid_until is not None and evaluated_at >= valid_until:
        return OrderTerminalReason.ENTRY_STALE
    return None


__all__ = [
    "ATLAS_DAY_END",
    "ATLAS_DAY_START",
    "LEGACY_WARRIOR_ENTRY_VALIDITY",
    "WARRIOR_LIFECYCLE_PREFIX",
    "atlas_day_expiration",
    "entry_valid_until",
    "is_autonomous_warrior_entry",
    "temporal_terminal_reason",
]
