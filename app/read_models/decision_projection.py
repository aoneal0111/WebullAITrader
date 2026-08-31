from __future__ import annotations

from dataclasses import replace
from threading import RLock

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import (
    DecisionsUpdated,
    OperationsBus,
    OperationsDecisionRecord,
)
from app.read_models.decisions import (
    DecisionExecutionOutcome,
    DecisionRecord,
    DecisionsReadModelSnapshot,
)


_PROGRESSION = {
    DecisionExecutionOutcome.PENDING: 0,
    DecisionExecutionOutcome.ACCEPTED: 1,
    DecisionExecutionOutcome.PARTIALLY_FILLED: 2,
    DecisionExecutionOutcome.FILLED: 3,
    DecisionExecutionOutcome.REJECTED: 3,
    DecisionExecutionOutcome.CANCELLED: 3,
}


class DecisionProjection:
    """Correlate structured runtime decisions with order execution facts."""

    def __init__(self, bus: OperationsBus) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        self._bus = bus
        self._lock = RLock()
        self._snapshot = DecisionsReadModelSnapshot.initial()
        self._outcomes_by_order_id: dict[
            str, DecisionExecutionOutcome
        ] = {}

    @property
    def snapshot(self) -> DecisionsReadModelSnapshot:
        with self._lock:
            return self._snapshot

    def __call__(self, event: PaperRuntimeEvent) -> None:
        if not isinstance(event, PaperRuntimeEvent):
            raise TypeError("event must be a PaperRuntimeEvent")
        if event.decision is None and event.order is None and event.fill is None:
            return
        with self._lock:
            current = self._snapshot
            outcome = _event_outcome(event)
            order_id = _event_order_id(event)
            if order_id is not None and outcome is not None:
                previous = self._outcomes_by_order_id.get(order_id)
                self._outcomes_by_order_id[order_id] = _advance(
                    previous,
                    outcome,
                )

            by_id = {
                item.decision_id: item
                for item in current.decisions
            }
            if event.decision is not None:
                item = event.decision
                record = DecisionRecord(
                    decision_id=item.decision_id,
                    timestamp=item.timestamp,
                    strategy_id=item.strategy_id,
                    symbol=item.symbol,
                    action=item.action,
                    confidence=item.confidence,
                    reasoning_summary=item.reasoning_summary,
                    risk_assessment=item.risk_assessment,
                    requested_quantity=(
                        format(item.requested_quantity, "f")
                        if item.requested_quantity is not None
                        else None
                    ),
                    resulting_order_id=item.resulting_order_id,
                    execution_outcome=DecisionExecutionOutcome.PENDING,
                )
                if record.resulting_order_id is not None:
                    known = self._outcomes_by_order_id.get(
                        record.resulting_order_id
                    )
                    if known is not None:
                        record = replace(
                            record,
                            execution_outcome=known,
                        )
                by_id[record.decision_id] = record

            if order_id is not None and outcome is not None:
                for decision_id, record in tuple(by_id.items()):
                    if record.resulting_order_id == order_id:
                        by_id[decision_id] = replace(
                            record,
                            execution_outcome=_advance(
                                record.execution_outcome,
                                outcome,
                            ),
                        )

            projected = DecisionsReadModelSnapshot(
                decisions=tuple(
                    sorted(
                        by_id.values(),
                        key=lambda item: (
                            item.timestamp,
                            item.decision_id,
                        ),
                        reverse=True,
                    )
                )
            )
            if projected == current:
                return
            self._snapshot = projected
            operations_records = tuple(
                _to_operations(item)
                for item in projected.decisions
            )

        self._bus.publish(
            DecisionsUpdated(
                occurred_at=event.timestamp,
                source="paper-runtime-decision-projection",
                decisions=operations_records,
            )
        )


def _event_order_id(event: PaperRuntimeEvent) -> str | None:
    if event.order is not None:
        return event.order.order_id
    if event.fill is not None:
        return event.fill.request_id
    return None


def _event_outcome(
    event: PaperRuntimeEvent,
) -> DecisionExecutionOutcome | None:
    if event.order is not None:
        normalized = event.order.status.upper().replace(" ", "_")
        aliases = {
            "NEW": DecisionExecutionOutcome.PENDING,
            "PENDING": DecisionExecutionOutcome.PENDING,
            "SUBMITTED": DecisionExecutionOutcome.PENDING,
            "ACCEPTED": DecisionExecutionOutcome.ACCEPTED,
            "PARTIAL_FILL": DecisionExecutionOutcome.PARTIALLY_FILLED,
            "PARTIALLY_FILLED": DecisionExecutionOutcome.PARTIALLY_FILLED,
            "FILLED": DecisionExecutionOutcome.FILLED,
            "REJECTED": DecisionExecutionOutcome.REJECTED,
            "CANCELED": DecisionExecutionOutcome.CANCELLED,
            "CANCELLED": DecisionExecutionOutcome.CANCELLED,
        }
        return aliases.get(normalized)
    if event.fill is not None:
        return DecisionExecutionOutcome.FILLED
    return None


def _advance(
    current: DecisionExecutionOutcome | None,
    incoming: DecisionExecutionOutcome,
) -> DecisionExecutionOutcome:
    if current is None:
        return incoming
    if _PROGRESSION[incoming] > _PROGRESSION[current]:
        return incoming
    return current


def _to_operations(item: DecisionRecord) -> OperationsDecisionRecord:
    return OperationsDecisionRecord(
        decision_id=item.decision_id,
        timestamp=item.timestamp,
        strategy_id=item.strategy_id,
        symbol=item.symbol,
        action=item.action,
        confidence=item.confidence,
        reasoning_summary=item.reasoning_summary,
        risk_assessment=item.risk_assessment,
        requested_quantity=item.requested_quantity,
        resulting_order_id=item.resulting_order_id,
        execution_outcome=item.execution_outcome.value,
    )


__all__ = ["DecisionProjection"]
