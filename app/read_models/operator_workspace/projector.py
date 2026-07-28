from __future__ import annotations

from threading import RLock

from app.operations_core import (
    OperationsBus,
    OperatorDecisionSelected,
    OperatorSelectionEvent,
    OperatorSymbolSelected,
    OperatorTimelineSelected,
    OperatorTradeSelected,
    Subscription,
)

from .models import (
    OperatorWorkspaceSnapshot,
    WorkspaceSelectionSource,
)


class OperatorWorkspaceProjector:
    """Reduce immutable selection events into shared workspace state."""

    def __init__(self, bus: OperationsBus) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        self._bus = bus
        self._lock = RLock()
        self._snapshot = OperatorWorkspaceSnapshot.initial()
        self._subscription: Subscription | None = bus.subscribe(
            OperatorSelectionEvent,
            self._handle_selection,
        )

    def snapshot(self) -> OperatorWorkspaceSnapshot:
        with self._lock:
            return self._snapshot

    def close(self) -> None:
        subscription = self._subscription
        if subscription is not None:
            self._bus.unsubscribe(subscription)
            self._subscription = None

    def _handle_selection(
        self,
        event: OperatorSelectionEvent,
    ) -> None:
        with self._lock:
            self._snapshot = _reduce(self._snapshot, event)


def _reduce(
    current: OperatorWorkspaceSnapshot,
    event: OperatorSelectionEvent,
) -> OperatorWorkspaceSnapshot:
    if isinstance(event, OperatorTradeSelected):
        return OperatorWorkspaceSnapshot(
            selected_symbol=event.symbol,
            selected_trade=event.symbol,
            selection_source=WorkspaceSelectionSource.TRADE,
        )
    if isinstance(event, OperatorDecisionSelected):
        return OperatorWorkspaceSnapshot(
            selected_symbol=event.symbol,
            selected_decision=event.decision_id,
            selection_source=WorkspaceSelectionSource.DECISION,
        )
    if isinstance(event, OperatorTimelineSelected):
        return OperatorWorkspaceSnapshot(
            selected_symbol=(
                current.selected_symbol
                if event.symbol is None
                else event.symbol
            ),
            selected_timeline_entry=event.timeline_entry_id,
            selection_source=WorkspaceSelectionSource.TIMELINE,
        )
    if isinstance(event, OperatorSymbolSelected):
        source = WorkspaceSelectionSource(event.selection_source)
        values = {
            "selected_symbol": event.symbol,
            "selection_source": source,
        }
        if source is WorkspaceSelectionSource.POSITION:
            values["selected_position"] = event.symbol
        elif source is WorkspaceSelectionSource.ORDER:
            values["selected_order"] = event.selection_id
        return OperatorWorkspaceSnapshot(**values)
    return current
