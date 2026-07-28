from __future__ import annotations

from threading import RLock

from app.operations_core import (
    DecisionsUpdated,
    OperationsBus,
    OperationsDecision,
    RuntimeStarting,
    Subscription,
)

from .models import DecisionReadModel, DecisionsReadModelSnapshot


class DecisionProjector:
    """Thread-safe event consumer exposing immutable decision snapshots."""

    def __init__(self, bus: OperationsBus) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        self._bus = bus
        self._lock = RLock()
        self._snapshot = DecisionsReadModelSnapshot.initial()
        self._subscriptions: tuple[Subscription, ...] = (
            bus.subscribe(DecisionsUpdated, self._handle_decisions_updated),
            bus.subscribe(RuntimeStarting, self._handle_runtime_starting),
        )

    def snapshot(self) -> DecisionsReadModelSnapshot:
        with self._lock:
            return self._snapshot

    def close(self) -> None:
        for subscription in self._subscriptions:
            self._bus.unsubscribe(subscription)
        self._subscriptions = ()

    def _handle_decisions_updated(self, event: DecisionsUpdated) -> None:
        with self._lock:
            self._snapshot = DecisionsReadModelSnapshot(
                cycle=event.cycle,
                updated_at=event.occurred_at,
                decisions=tuple(
                    _project_decision(decision)
                    for decision in event.decisions
                ),
            )

    def _handle_runtime_starting(self, event: RuntimeStarting) -> None:
        del event
        with self._lock:
            self._snapshot = DecisionsReadModelSnapshot.initial()


def _project_decision(decision: OperationsDecision) -> DecisionReadModel:
    return DecisionReadModel(
        symbol=decision.symbol,
        action=decision.action,
        confidence=decision.confidence,
        score=decision.score,
        reasons=decision.reasons,
        source_action=decision.source_action,
        position_quantity=decision.position_quantity,
        strategy_version=decision.strategy_version,
        decided_at=decision.decided_at,
    )
