"""Deterministic presentation-only merging of broker and PAPER projections."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.operations_core.events import OperationsOrder, OperationsPosition


ACTIVE_ORDER_STATUSES = frozenset(
    {
        "ACCEPTED",
        "ACKNOWLEDGED",
        "NEW",
        "OPEN",
        "PARTIAL_FILL",
        "PARTIALLY_FILLED",
        "PENDING",
        "SUBMITTED",
        "WORKING",
    }
)
TERMINAL_ORDER_STATUSES = frozenset(
    {"CANCELLED", "CANCELED", "EXPIRED", "FILLED", "REJECTED"}
)


def normalize_order_status(status: str) -> str:
    return status.strip().upper().replace(" ", "_")


def is_active_order(order: OperationsOrder) -> bool:
    """Return working exposure from lifecycle status, never remaining text."""

    return normalize_order_status(order.status) in ACTIVE_ORDER_STATUSES


def merge_orders(
    broker_orders: tuple[OperationsOrder, ...],
    paper_orders: tuple[OperationsOrder, ...],
) -> tuple[OperationsOrder, ...]:
    """Merge complete source snapshots into one order view.

    Order id is the only cross-source logical identity currently available.
    A terminal fact dominates an obsolete nonterminal fact for that identity;
    otherwise the newest fact wins with PAPER as a stable final tie-breaker.
    The result always places every active order before bounded-at-render-time
    terminal history.
    """

    selected: dict[str, tuple[OperationsOrder, int]] = {}
    for source_rank, orders in ((0, broker_orders), (1, paper_orders)):
        for order in orders:
            candidate = (order, source_rank)
            current = selected.get(order.order_id)
            if current is None or _order_precedence(candidate) > _order_precedence(
                current
            ):
                selected[order.order_id] = candidate
    return tuple(
        item[0]
        for item in sorted(
            selected.values(),
            key=lambda item: (
                is_active_order(item[0]),
                item[0].updated_at,
                item[0].order_id,
                item[1],
            ),
            reverse=True,
        )
    )


def merge_positions(
    broker_positions: tuple[OperationsPosition, ...],
    paper_positions: tuple[OperationsPosition, ...],
    *,
    environment: str,
) -> tuple[OperationsPosition, ...]:
    """Merge current exposure and retained PAPER history deterministically.

    Identity is account plus symbol.  Nonzero exposure always dominates a flat
    historical row.  On a true same-account collision PAPER wins in PAPER mode
    and broker-current wins otherwise.  Flat PAPER rows remain available as
    history, but presentation formatters—not this source merge—decide where
    historical rows are rendered.
    """

    paper_preferred = environment.strip().upper() == "PAPER"
    selected: dict[tuple[str, str], tuple[OperationsPosition, int]] = {}
    sources = (
        (1 if not paper_preferred else 0, broker_positions),
        (1 if paper_preferred else 0, paper_positions),
    )
    for source_rank, positions in sources:
        for position in positions:
            key = (position.account_id, position.symbol)
            candidate = (position, source_rank)
            current = selected.get(key)
            if current is None or _position_precedence(
                candidate
            ) > _position_precedence(current):
                selected[key] = candidate
    return tuple(
        item[0]
        for item in sorted(
            selected.values(),
            key=lambda item: (
                _quantity(item[0]) != 0,
                item[0].symbol,
                item[0].account_id,
            ),
            reverse=True,
        )
    )


def _order_precedence(item: tuple[OperationsOrder, int]) -> tuple:
    order, source_rank = item
    terminal = normalize_order_status(order.status) in TERMINAL_ORDER_STATUSES
    return terminal, order.updated_at, source_rank


def _position_precedence(item: tuple[OperationsPosition, int]) -> tuple:
    position, source_rank = item
    return _quantity(position) != 0, source_rank, position.updated_at


def _quantity(position: OperationsPosition) -> Decimal:
    try:
        value = Decimal(position.quantity)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("position quantity must be Decimal-compatible") from exc
    if not value.is_finite():
        raise ValueError("position quantity must be finite")
    return value


__all__ = [
    "ACTIVE_ORDER_STATUSES",
    "TERMINAL_ORDER_STATUSES",
    "is_active_order",
    "merge_orders",
    "merge_positions",
    "normalize_order_status",
]
