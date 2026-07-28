from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from threading import RLock

from app.operations_core import (
    DecisionsUpdated,
    OperationsBus,
    OperationsEvent,
    OrdersUpdated,
    PaperOrderLifecycleUpdated,
    PositionsUpdated,
    RuntimeCycleCompleted,
    RuntimeFailed,
    RuntimeStopped,
    Subscription,
    TradeLifecycleUpdated,
)

from .models import (
    TradeLifecycle,
    TradeLifecycleEntry,
    TradeLifecyclePhase,
    TradeLifecycleSnapshot,
    TradeLifecycleStatus,
)


class TradeLifecycleProjector:
    """Reconstruct immutable, symbol-scoped trade histories from events."""

    def __init__(self, bus: OperationsBus) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        self._bus = bus
        self._lock = RLock()
        self._snapshot = TradeLifecycleSnapshot.initial()
        self._subscription: Subscription | None = bus.subscribe(
            OperationsEvent,
            self._handle_event,
        )

    def snapshot(self) -> TradeLifecycleSnapshot:
        with self._lock:
            return self._snapshot

    def close(self) -> None:
        subscription = self._subscription
        if subscription is not None:
            self._bus.unsubscribe(subscription)
            self._subscription = None

    def _handle_event(self, event: OperationsEvent) -> None:
        with self._lock:
            self._snapshot = _reduce(self._snapshot, event)


def _reduce(
    snapshot: TradeLifecycleSnapshot,
    event: OperationsEvent,
) -> TradeLifecycleSnapshot:
    if isinstance(event, TradeLifecycleUpdated):
        try:
            phase = TradeLifecyclePhase(event.phase)
        except ValueError:
            phase = TradeLifecyclePhase.ERROR
        return _append(
            snapshot,
            _entry(
                event,
                symbol=event.symbol,
                phase=phase,
                title=event.title,
                description=event.description,
                order_id=event.order_id,
                position_id=event.position_id,
                cycle=event.cycle,
            ),
            realized_pnl=event.realized_pnl,
        )

    if isinstance(event, DecisionsUpdated):
        current = snapshot
        for decision in event.decisions:
            action = decision.action.upper()
            is_non_trade = action in {"HOLD", "IGNORE"}
            phase = (
                TradeLifecyclePhase.EXIT
                if "EXIT" in action
                else TradeLifecyclePhase.DECISION
            )
            current = _append(
                current,
                _entry(
                    event,
                    symbol=decision.symbol,
                    phase=phase,
                    title=action.replace("_", " "),
                    description=(
                        " | ".join(decision.reasons)
                        if decision.reasons
                        else f"Strategy selected {action}."
                    ),
                    cycle=event.cycle,
                ),
                opens=not is_non_trade,
            )
        return current

    if isinstance(event, OrdersUpdated):
        current = snapshot
        for order in event.orders:
            phase, status = _order_phase(order.status)
            current = _append(
                current,
                _entry(
                    event,
                    symbol=order.symbol.strip().upper(),
                    phase=phase,
                    title=_phase_title(phase),
                    description=(
                        f"{order.side} {order.quantity} order "
                        f"{order.order_id} is {order.status}."
                    ),
                    order_id=order.order_id,
                ),
                status=status,
            )
        return current

    if isinstance(event, PaperOrderLifecycleUpdated):
        symbol = event.symbol or _symbol_for_order(
            snapshot,
            event.order_id,
        )
        if symbol is None:
            return snapshot
        phase, status = _order_phase(event.current_status)
        return _append(
            snapshot,
            _entry(
                event,
                symbol=symbol,
                phase=phase,
                title=_phase_title(phase),
                description=(
                    f"Order {event.order_id} moved from "
                    f"{event.previous_status} to {event.current_status}."
                ),
                order_id=event.order_id,
            ),
            status=status,
        )

    if isinstance(event, PositionsUpdated):
        return _reduce_positions(snapshot, event)

    if isinstance(event, RuntimeCycleCompleted):
        current = snapshot
        for lifecycle in snapshot.lifecycles:
            if lifecycle.status is not TradeLifecycleStatus.OPEN:
                continue
            current = _append(
                current,
                _entry(
                    event,
                    symbol=lifecycle.symbol,
                    phase=TradeLifecyclePhase.RISK_UPDATE,
                    title="Runtime cycle completed",
                    description=(
                        f"Trade remained active after cycle "
                        f"{event.cycle_count}."
                    ),
                    cycle=event.cycle_count,
                ),
            )
        return current

    if isinstance(event, RuntimeStopped):
        current = snapshot
        for lifecycle in snapshot.lifecycles:
            if lifecycle.status is not TradeLifecycleStatus.OPEN:
                continue
            current = _append(
                current,
                _entry(
                    event,
                    symbol=lifecycle.symbol,
                    phase=TradeLifecyclePhase.EXIT,
                    title="Runtime stopped",
                    description=event.reason.strip() or "Runtime stopped.",
                    cycle=event.cycles_completed,
                ),
                status=TradeLifecycleStatus.CLOSED,
            )
        return current

    if isinstance(event, RuntimeFailed):
        current = snapshot
        for lifecycle in snapshot.lifecycles:
            if lifecycle.status is TradeLifecycleStatus.CLOSED:
                continue
            current = _append(
                current,
                _entry(
                    event,
                    symbol=lifecycle.symbol,
                    phase=TradeLifecyclePhase.ERROR,
                    title="Runtime failed",
                    description=event.error_message.strip(),
                ),
                status=TradeLifecycleStatus.FAILED,
            )
        return current

    return snapshot


def _reduce_positions(
    snapshot: TradeLifecycleSnapshot,
    event: PositionsUpdated,
) -> TradeLifecycleSnapshot:
    current = snapshot
    symbols_present: set[str] = set()
    grouped: dict[str, list] = {}
    for position in event.positions:
        symbol = position.symbol.strip().upper()
        grouped.setdefault(symbol, []).append(position)

    for symbol, positions in grouped.items():
        symbols_present.add(symbol)
        quantity = sum(
            (_decimal(position.quantity) or Decimal("0"))
            for position in positions
        )
        realized_values = tuple(
            value
            for value in (
                _decimal(position.realized_gain_loss)
                for position in positions
            )
            if value is not None
        )
        realized_pnl = (
            sum(realized_values, Decimal("0"))
            if realized_values
            else None
        )
        phase = (
            TradeLifecyclePhase.POSITION_OPEN
            if quantity != Decimal("0")
            else TradeLifecyclePhase.POSITION_CLOSE
        )
        status = (
            TradeLifecycleStatus.OPEN
            if phase is TradeLifecyclePhase.POSITION_OPEN
            else TradeLifecycleStatus.CLOSED
        )
        position_id = (
            f"{positions[0].account_id}:{symbol}"
            if len(positions) == 1
            else None
        )
        current = _append(
            current,
            _entry(
                event,
                symbol=symbol,
                phase=phase,
                title=_phase_title(phase),
                description=(
                    f"Position quantity is {quantity:f}; "
                    f"market value is {positions[0].market_value}."
                ),
                position_id=position_id,
            ),
            status=status,
            realized_pnl=realized_pnl,
        )

    for lifecycle in snapshot.lifecycles:
        if (
            lifecycle.status is TradeLifecycleStatus.OPEN
            and lifecycle.symbol not in symbols_present
            and any(
                entry.phase is TradeLifecyclePhase.POSITION_OPEN
                for entry in lifecycle.entries
            )
        ):
            current = _append(
                current,
                _entry(
                    event,
                    symbol=lifecycle.symbol,
                    phase=TradeLifecyclePhase.POSITION_CLOSE,
                    title="Position closed",
                    description=(
                        "Position no longer appears in the authoritative "
                        "position snapshot."
                    ),
                ),
                status=TradeLifecycleStatus.CLOSED,
            )
    return current


def _append(
    snapshot: TradeLifecycleSnapshot,
    entry: TradeLifecycleEntry,
    *,
    status: TradeLifecycleStatus | None = None,
    realized_pnl: Decimal | None = None,
    opens: bool = True,
) -> TradeLifecycleSnapshot:
    by_symbol = {
        lifecycle.symbol: lifecycle
        for lifecycle in snapshot.lifecycles
    }
    current = by_symbol.get(entry.symbol)
    if current is None:
        current = TradeLifecycle(
            symbol=entry.symbol,
            entries=(),
            status=TradeLifecycleStatus.UNKNOWN,
            opened_at=None,
            closed_at=None,
            realized_pnl=Decimal("0"),
        )

    next_status = status
    if next_status is None:
        if entry.phase is TradeLifecyclePhase.ERROR:
            next_status = TradeLifecycleStatus.FAILED
        elif entry.phase in {
            TradeLifecyclePhase.POSITION_CLOSE,
            TradeLifecyclePhase.EXIT,
        }:
            next_status = TradeLifecycleStatus.CLOSED
        elif opens:
            next_status = TradeLifecycleStatus.OPEN
        else:
            next_status = current.status

    opened_at = current.opened_at
    closed_at = current.closed_at
    if next_status is TradeLifecycleStatus.OPEN:
        if current.status in {
            TradeLifecycleStatus.CLOSED,
            TradeLifecycleStatus.FAILED,
        } or opened_at is None:
            opened_at = entry.timestamp
        closed_at = None
    elif next_status is TradeLifecycleStatus.CLOSED:
        opened_at = opened_at or entry.timestamp
        closed_at = entry.timestamp
    elif next_status is TradeLifecycleStatus.FAILED:
        closed_at = entry.timestamp if opened_at is not None else None

    by_symbol[entry.symbol] = replace(
        current,
        entries=current.entries + (entry,),
        status=next_status,
        opened_at=opened_at,
        closed_at=closed_at,
        realized_pnl=(
            current.realized_pnl
            if realized_pnl is None
            else realized_pnl
        ),
    )
    return TradeLifecycleSnapshot(
        lifecycles=tuple(
            by_symbol[symbol]
            for symbol in sorted(by_symbol)
        ),
        selected_symbol=entry.symbol,
    )


def _entry(
    event: OperationsEvent,
    *,
    symbol: str,
    phase: TradeLifecyclePhase,
    title: str,
    description: str,
    order_id: str | None = None,
    position_id: str | None = None,
    cycle: int | None = None,
) -> TradeLifecycleEntry:
    return TradeLifecycleEntry(
        timestamp=event.occurred_at,
        phase=phase,
        title=title,
        description=description,
        symbol=symbol.strip().upper(),
        order_id=order_id,
        position_id=position_id,
        cycle=cycle,
    )


def _order_phase(
    status: str,
) -> tuple[TradeLifecyclePhase, TradeLifecycleStatus]:
    normalized = status.strip().upper()
    if normalized in {"ACCEPTED", "OPEN"}:
        return (
            TradeLifecyclePhase.ORDER_ACCEPTED,
            TradeLifecycleStatus.OPEN,
        )
    if normalized in {"PARTIALLY_FILLED", "PARTIAL_FILL"}:
        return (
            TradeLifecyclePhase.PARTIAL_FILL,
            TradeLifecycleStatus.OPEN,
        )
    if normalized == "FILLED":
        return TradeLifecyclePhase.FILLED, TradeLifecycleStatus.OPEN
    if normalized in {"REJECTED", "FAILED"}:
        return TradeLifecyclePhase.ERROR, TradeLifecycleStatus.FAILED
    if normalized in {
        "CANCELED",
        "CANCELLED",
        "EXPIRED",
    }:
        return TradeLifecyclePhase.EXIT, TradeLifecycleStatus.CLOSED
    return (
        TradeLifecyclePhase.ORDER_SUBMITTED,
        TradeLifecycleStatus.OPEN,
    )


def _phase_title(phase: TradeLifecyclePhase) -> str:
    return phase.value.replace("_", " ").title()


def _symbol_for_order(
    snapshot: TradeLifecycleSnapshot,
    order_id: str,
) -> str | None:
    for lifecycle in snapshot.lifecycles:
        if any(entry.order_id == order_id for entry in lifecycle.entries):
            return lifecycle.symbol
    return None


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None
