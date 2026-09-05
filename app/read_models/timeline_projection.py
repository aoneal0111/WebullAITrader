from __future__ import annotations

from threading import RLock

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import (
    OperationsBus,
    OperationsTimelineEntry,
    TimelineUpdated,
)
from app.read_models.timeline.models import (
    TimelineCategory,
    TimelineEntry,
    TimelineReadModelSnapshot,
    TimelineSeverity,
)
from app.performance_diagnostics import performance_diagnostics


_NOISY_EVENT_TYPES = frozenset(
    {
        "CYCLE_COMPLETED",
        "MARKET_DATA_HEARTBEAT",
        "QUOTE_UPDATED",
        "MARK_UPDATED",
        "MARKET_DATA_QUOTE_RECEIVED",
        "MARKET_DATA_TRADE_RECEIVED",
        "MARKET_DATA_SNAPSHOT_RECEIVED",
        "SCANNER_CYCLE",
        "EVENTS_CONSUMED",
    }
)


class TimelineProjection:
    """Fold significant runtime events into a bounded immutable timeline."""

    def __init__(
        self,
        bus: OperationsBus,
        *,
        maximum_entries: int = 500,
    ) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        if (
            isinstance(maximum_entries, bool)
            or not isinstance(maximum_entries, int)
            or maximum_entries < 1
        ):
            raise ValueError("maximum_entries must be a positive integer")

        self._bus = bus
        self._maximum_entries = maximum_entries
        self._lock = RLock()
        self._records: tuple[tuple[TimelineEntry, int], ...] = ()
        self._seen_events: frozenset[tuple[str, int]] = frozenset()
        self._snapshot = TimelineReadModelSnapshot.initial()

    @property
    def snapshot(self) -> TimelineReadModelSnapshot:
        with self._lock:
            return self._snapshot

    def memory_metrics(self) -> dict[str, int]:
        with self._lock:
            return {"timeline_count": len(self._records), "seen_event_count": len(self._seen_events)}

    def __call__(self, event: PaperRuntimeEvent) -> None:
        if not isinstance(event, PaperRuntimeEvent):
            raise TypeError("event must be a PaperRuntimeEvent")
        # Startup replay reconstructs state; it must not republish historical
        # fills as fresh live Activity entries.
        if event.source == "paper-execution-replay":
            return

        identity = (event.source, event.sequence)
        with self._lock:
            if identity in self._seen_events:
                return

            entry = _project_event(event)
            if entry is None:
                return

            current = self._snapshot
            records = (*self._records, (entry, event.sequence))
            records = tuple(
                sorted(
                    records,
                    key=lambda record: (
                        record[0].timestamp,
                        record[1],
                    ),
                    reverse=True,
                )[: self._maximum_entries]
            )
            self._records = records
            self._seen_events = frozenset(
                (record[0].source, record[1])
                for record in records
            )
            self._snapshot = TimelineReadModelSnapshot(
                entries=tuple(record[0] for record in records)
            )
            if self._snapshot == current:
                return
            performance_diagnostics.increment("event_store_rows_added")
            operations_entries = tuple(
                _to_operations_entry(item)
                for item in self._snapshot.entries
            )

        self._bus.publish(
            TimelineUpdated(
                occurred_at=event.timestamp,
                source="paper-runtime-timeline-projection",
                entries=operations_entries,
            )
        )


def _project_event(event: PaperRuntimeEvent) -> TimelineEntry | None:
    event_type = event.event_type.strip().upper()
    if event_type in _NOISY_EVENT_TYPES:
        return None
    if (
        event_type == "DECISION_PROCESSED"
        and event.order is None
        and event.fill is None
    ):
        return None

    category = _category(event, event_type)
    severity = _severity(event, event_type)
    title = _title(event, event_type)
    symbol = (
        event.symbol
        or (event.fill.symbol if event.fill is not None else None)
        or (event.order.symbol if event.order is not None else None)
    )
    order_id = (
        event.fill.request_id
        if event.fill is not None
        else event.order.order_id
        if event.order is not None
        else None
    )

    return TimelineEntry(
        timestamp=event.timestamp,
        category=category,
        severity=severity,
        source=event.source,
        title=title,
        description=event.message,
        related_symbol=symbol,
        related_order_id=order_id,
    )


def _category(
    event: PaperRuntimeEvent,
    event_type: str,
) -> TimelineCategory:
    if event.fill is not None or any(
        token in event_type
        for token in ("FILL", "EXECUTION")
    ):
        return TimelineCategory.EXECUTION
    if event.order is not None or any(
        token in event_type
        for token in (
            "ORDER",
            "SUBMIT",
            "ACCEPT",
            "CANCEL",
            "REJECT",
        )
    ):
        return TimelineCategory.ORDER
    if "BROKER" in event_type:
        return TimelineCategory.BROKER
    if any(
        token in event_type
        for token in ("MARKET_DATA", "MARKET_FEED", "QUOTE", "STREAM")
    ):
        return TimelineCategory.MARKET_DATA
    if any(
        token in event_type
        for token in ("AI", "MODEL", "INFERENCE")
    ):
        return TimelineCategory.AI
    if any(
        token in event_type
        for token in (
            "START",
            "STOP",
            "PAUSE",
            "RESUME",
            "RUNTIME",
            "FAILED",
        )
    ):
        return TimelineCategory.RUNTIME
    return TimelineCategory.SYSTEM


def _severity(
    event: PaperRuntimeEvent,
    event_type: str,
) -> TimelineSeverity:
    order_status = event.order.status.upper() if event.order else ""
    if any(
        token in event_type
        for token in ("ERROR", "FAILED", "FAILURE")
    ):
        return TimelineSeverity.ERROR
    if order_status in {
        "CANCELLED",
        "CANCELED",
        "NOT_FILLED",
        "REJECTED",
    }:
        return TimelineSeverity.WARNING
    if any(
        token in event_type
        for token in (
            "WARNING",
            "WARN",
            "VETO",
            "REJECT",
            "CANCEL",
            "DISCONNECT",
            "NOT_FILLED",
        )
    ):
        return TimelineSeverity.WARNING
    if event.fill is not None or order_status in {
        "ACCEPTED",
        "FILLED",
    }:
        return TimelineSeverity.SUCCESS
    if any(
        token in event_type
        for token in (
            "CONNECTED",
            "STARTED",
            "RESUMED",
            "LOADED",
            "COMPLETED",
        )
    ):
        return TimelineSeverity.SUCCESS
    return TimelineSeverity.INFO


def _title(event: PaperRuntimeEvent, event_type: str) -> str:
    if event.fill is not None:
        return "Order filled"
    if event.order is not None:
        return {
            "ACCEPTED": "Order accepted",
            "CANCELLED": "Order cancelled",
            "CANCELED": "Order cancelled",
            "FILLED": "Order filled",
            "REJECTED": "Order rejected",
            "NEW": "Order submitted",
            "SUBMITTED": "Order submitted",
        }.get(
            event.order.status.upper(),
            "Order updated",
        )

    known_titles = {
        "STARTED": "Runtime started",
        "STOPPED": "Runtime stopped",
        "PAUSED": "Runtime paused",
        "RESUMED": "Runtime resumed",
        "FAILED": "Runtime failed",
        "BROKER_CONNECTED": "Broker connected",
        "BROKER_DISCONNECTED": "Broker disconnected",
        "MARKET_DATA_CONNECTED": "Market data connected",
        "MARKET_DATA_DISCONNECTED": "Market data disconnected",
        "INFERENCE_VETO": "AI inference veto",
        "AI_STARTED": "AI started",
        "AI_STOPPED": "AI stopped",
        "MODEL_LOADED": "AI model loaded",
    }
    return known_titles.get(
        event_type,
        event_type.replace("_", " ").title(),
    )


def _to_operations_entry(
    entry: TimelineEntry,
) -> OperationsTimelineEntry:
    return OperationsTimelineEntry(
        timestamp=entry.timestamp,
        category=entry.category.value,
        severity=entry.severity.value,
        source=entry.source,
        title=entry.title,
        description=entry.description,
        related_symbol=entry.related_symbol,
        related_order_id=entry.related_order_id,
    )


__all__ = ["TimelineProjection"]
