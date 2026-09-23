"""Bounded, fair latest-state work scheduling for observational evaluation."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from threading import Lock


@dataclass(frozen=True, slots=True)
class EvaluationWork:
    symbol: str
    version: int
    enqueued_at: datetime
    event: object


class LatestEvaluationMailbox:
    """One pending observational evaluation per symbol.

    Replacing a symbol's work never changes its position in the ordered
    scheduler. This makes a hot symbol unable to move continuously ahead of
    symbols that are already waiting. The canonical adapter state is updated
    before this mailbox is touched, so mailbox pressure cannot lose market
    state or authoritative events.
    """

    def __init__(self, *, capacity: int = 256) -> None:
        if capacity <= 0:
            raise ValueError("evaluation mailbox capacity must be positive")
        self.capacity = int(capacity)
        self._pending: OrderedDict[str, EvaluationWork] = OrderedDict()
        self._lock = Lock()
        self._high_water = 0
        self._produced = 0
        self._superseded = 0
        self._overflow = 0
        self._dequeued = 0

    def enqueue(
        self,
        symbol: str,
        version: int,
        enqueued_at: datetime,
        event: object,
    ) -> bool:
        normalized = symbol.strip().upper()
        if not normalized:
            return False
        if version <= 0:
            raise ValueError("evaluation versions must be positive")
        with self._lock:
            if normalized in self._pending:
                self._superseded += 1
                # Assignment replaces the latest immutable work while the
                # OrderedDict position remains stable for fairness.
                self._pending[normalized] = EvaluationWork(
                    normalized, version, enqueued_at, event,
                )
                self._produced += 1
                return True
            if len(self._pending) >= self.capacity:
                self._overflow += 1
                return False
            self._pending[normalized] = EvaluationWork(
                normalized, version, enqueued_at, event,
            )
            self._produced += 1
            self._high_water = max(self._high_water, len(self._pending))
            return True

    def pop(self) -> EvaluationWork | None:
        with self._lock:
            if not self._pending:
                return None
            _symbol, work = self._pending.popitem(last=False)
            self._dequeued += 1
            return work

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)

    def metrics(self, *, now: datetime | None = None) -> dict[str, object]:
        with self._lock:
            pending = tuple(self._pending.values())
            current_age = None
            oldest_age = None
            if now is not None and pending:
                ages = tuple(
                    max(0.0, (now - item.enqueued_at).total_seconds())
                    for item in pending
                )
                current_age = max(ages)
                oldest_age = max(ages)
            return {
                "evaluation_mailbox_size": len(pending),
                "evaluation_mailbox_high_water": self._high_water,
                "pending_symbol_work": len(pending),
                "pending_symbols": tuple(item.symbol for item in pending),
                "evaluation_age_seconds": current_age,
                "oldest_pending_evaluation_age_seconds": oldest_age,
                "symbol_snapshots_produced": self._produced,
                "symbol_snapshots_superseded": self._superseded,
                "evaluation_work_dequeued": self._dequeued,
                "evaluation_mailbox_overflow": self._overflow,
            }


__all__ = ["EvaluationWork", "LatestEvaluationMailbox"]
