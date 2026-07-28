from __future__ import annotations

import re
from threading import RLock

from app.operations_core import (
    DecisionsUpdated,
    OperationsBus,
    OperationsEvent,
    OrdersUpdated,
    PaperOrderLifecycleUpdated,
    PaperRuntimeUpdated,
    PositionsUpdated,
    RuntimeCycleCompleted,
    RuntimeFailed,
    RuntimeStarted,
    RuntimeStarting,
    RuntimeStopped,
    RuntimeStopping,
    Subscription,
    TradeLifecycleUpdated,
)

from .models import (
    TimelineCategory,
    TimelineEntry,
    TimelineSeverity,
    TimelineSnapshot,
)


class TimelineProjector:
    """Build a bounded, newest-first history from OperationsBus events."""

    def __init__(
        self,
        bus: OperationsBus,
        *,
        max_entries: int = 500,
    ) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        initial = TimelineSnapshot.initial(max_entries=max_entries)
        self._bus = bus
        self._lock = RLock()
        self._snapshot = initial
        self._subscription: Subscription | None = bus.subscribe(
            OperationsEvent,
            self._handle_event,
        )

    def snapshot(self) -> TimelineSnapshot:
        with self._lock:
            return self._snapshot

    def close(self) -> None:
        subscription = self._subscription
        if subscription is not None:
            self._bus.unsubscribe(subscription)
            self._subscription = None

    def _handle_event(self, event: OperationsEvent) -> None:
        entry = _project_event(event)
        with self._lock:
            entries = (entry,) + self._snapshot.entries
            self._snapshot = TimelineSnapshot(
                entries=entries[: self._snapshot.max_entries],
                max_entries=self._snapshot.max_entries,
            )


def _project_event(event: OperationsEvent) -> TimelineEntry:
    if isinstance(event, TradeLifecycleUpdated):
        category, severity = _trade_lifecycle_classification(event.phase)
        return _entry(
            event,
            category,
            severity,
            event.title,
            event.description,
            cycle=event.cycle,
            symbol=event.symbol,
        )
    if isinstance(event, RuntimeStarting):
        return _entry(
            event,
            TimelineCategory.SYSTEM,
            TimelineSeverity.INFO,
            "Runtime starting",
            f"Starting {event.environment} runtime.",
        )
    if isinstance(event, RuntimeStarted):
        return _entry(
            event,
            TimelineCategory.SYSTEM,
            TimelineSeverity.SUCCESS,
            "Runtime started",
            (
                f"{event.environment} runtime started with "
                f"{event.active_model}."
            ),
        )
    if isinstance(event, RuntimeStopping):
        return _entry(
            event,
            TimelineCategory.WARNING,
            TimelineSeverity.WARNING,
            "Runtime stopping",
            event.reason.strip() or "Runtime shutdown requested.",
        )
    if isinstance(event, RuntimeStopped):
        return _entry(
            event,
            TimelineCategory.SYSTEM,
            TimelineSeverity.INFO,
            "Runtime stopped",
            event.reason.strip() or "Runtime stopped.",
            cycle=event.cycles_completed,
        )
    if isinstance(event, RuntimeFailed):
        return _entry(
            event,
            TimelineCategory.ERROR,
            TimelineSeverity.ERROR,
            "Runtime failed",
            event.error_message.strip(),
        )
    if isinstance(event, PaperRuntimeUpdated):
        snapshot = event.snapshot
        return _entry(
            event,
            TimelineCategory.SYSTEM,
            TimelineSeverity.SUCCESS,
            "Paper runtime updated",
            (
                f"Cycle {snapshot.cycle} projected for "
                f"{len(snapshot.symbols)} symbols."
            ),
            cycle=snapshot.cycle,
            symbol=_single_symbol(snapshot.symbols),
        )
    if isinstance(event, DecisionsUpdated):
        count = len(event.decisions)
        return _entry(
            event,
            TimelineCategory.DECISION,
            (
                TimelineSeverity.SUCCESS
                if count
                else TimelineSeverity.INFO
            ),
            "Strategy decisions updated",
            f"Cycle {event.cycle} produced {count} decisions.",
            cycle=event.cycle,
            symbol=_single_symbol(
                tuple(decision.symbol for decision in event.decisions)
            ),
        )
    if isinstance(event, PaperOrderLifecycleUpdated):
        status = event.current_status.upper()
        category = (
            TimelineCategory.FILL
            if "FILL" in status
            else TimelineCategory.ORDER
        )
        severity = (
            TimelineSeverity.SUCCESS
            if "FILL" in status
            else (
                TimelineSeverity.WARNING
                if status in {"REJECTED", "CANCELED", "CANCELLED"}
                else TimelineSeverity.INFO
            )
        )
        return _entry(
            event,
            category,
            severity,
            "Paper order lifecycle updated",
            (
                f"Order {event.order_id} moved from "
                f"{event.previous_status} to {event.current_status}."
            ),
            symbol=event.symbol,
        )
    if isinstance(event, OrdersUpdated):
        return _entry(
            event,
            TimelineCategory.ORDER,
            TimelineSeverity.INFO,
            "Orders updated",
            f"Projected {len(event.orders)} orders.",
            symbol=_single_symbol(
                tuple(order.symbol for order in event.orders)
            ),
        )
    if isinstance(event, PositionsUpdated):
        return _entry(
            event,
            TimelineCategory.POSITION,
            TimelineSeverity.INFO,
            "Positions updated",
            f"Projected {len(event.positions)} positions.",
            symbol=_single_symbol(
                tuple(position.symbol for position in event.positions)
            ),
        )
    if isinstance(event, RuntimeCycleCompleted):
        return _entry(
            event,
            TimelineCategory.SYSTEM,
            TimelineSeverity.SUCCESS,
            "Runtime cycle completed",
            f"Completed runtime cycle {event.cycle_count}.",
            cycle=event.cycle_count,
        )

    event_name = type(event).__name__
    category, severity = _infer_classification(event_name, event.source)
    return _entry(
        event,
        category,
        severity,
        _humanize(event_name),
        f"Received {event_name} from {event.source}.",
    )


def _entry(
    event: OperationsEvent,
    category: TimelineCategory,
    severity: TimelineSeverity,
    title: str,
    description: str,
    *,
    cycle: int | None = None,
    symbol: str | None = None,
) -> TimelineEntry:
    return TimelineEntry(
        timestamp=event.occurred_at,
        category=category,
        severity=severity,
        title=title,
        description=description,
        cycle=cycle,
        symbol=symbol,
    )


def _single_symbol(symbols: tuple[str, ...]) -> str | None:
    unique = tuple(
        dict.fromkeys(
            symbol.strip().upper()
            for symbol in symbols
            if symbol.strip()
        )
    )
    return unique[0] if len(unique) == 1 else None


def _infer_classification(
    event_name: str,
    source: str,
) -> tuple[TimelineCategory, TimelineSeverity]:
    text = f"{event_name} {source}".lower()
    classifications = (
        ("error", TimelineCategory.ERROR, TimelineSeverity.ERROR),
        ("fail", TimelineCategory.ERROR, TimelineSeverity.ERROR),
        ("warn", TimelineCategory.WARNING, TimelineSeverity.WARNING),
        ("scanner", TimelineCategory.SCANNER, TimelineSeverity.INFO),
        ("evidence", TimelineCategory.EVIDENCE, TimelineSeverity.INFO),
        ("committee", TimelineCategory.COMMITTEE, TimelineSeverity.INFO),
        ("decision", TimelineCategory.DECISION, TimelineSeverity.INFO),
        ("fill", TimelineCategory.FILL, TimelineSeverity.SUCCESS),
        ("order", TimelineCategory.ORDER, TimelineSeverity.INFO),
        ("position", TimelineCategory.POSITION, TimelineSeverity.INFO),
        ("risk", TimelineCategory.RISK, TimelineSeverity.INFO),
        ("exit", TimelineCategory.EXIT, TimelineSeverity.INFO),
    )
    for token, category, severity in classifications:
        if token in text:
            return category, severity
    return TimelineCategory.SYSTEM, TimelineSeverity.INFO


def _humanize(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", value).strip()


def _trade_lifecycle_classification(
    phase: str,
) -> tuple[TimelineCategory, TimelineSeverity]:
    normalized = phase.strip().upper()
    categories = {
        "SCANNED": TimelineCategory.SCANNER,
        "EVIDENCE": TimelineCategory.EVIDENCE,
        "COMMITTEE": TimelineCategory.COMMITTEE,
        "DECISION": TimelineCategory.DECISION,
        "ORDER_SUBMITTED": TimelineCategory.ORDER,
        "ORDER_ACCEPTED": TimelineCategory.ORDER,
        "PARTIAL_FILL": TimelineCategory.FILL,
        "FILLED": TimelineCategory.FILL,
        "POSITION_OPEN": TimelineCategory.POSITION,
        "RISK_UPDATE": TimelineCategory.RISK,
        "STOP_UPDATED": TimelineCategory.RISK,
        "TARGET_UPDATED": TimelineCategory.RISK,
        "POSITION_CLOSE": TimelineCategory.POSITION,
        "EXIT": TimelineCategory.EXIT,
        "ERROR": TimelineCategory.ERROR,
    }
    category = categories.get(normalized, TimelineCategory.SYSTEM)
    if normalized == "ERROR":
        severity = TimelineSeverity.ERROR
    elif normalized in {"FILLED", "POSITION_OPEN", "POSITION_CLOSE", "EXIT"}:
        severity = TimelineSeverity.SUCCESS
    else:
        severity = TimelineSeverity.INFO
    return category, severity
