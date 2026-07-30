from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DecisionExecutionOutcome(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    decision_id: str
    timestamp: datetime
    strategy_id: str
    symbol: str
    action: str
    confidence: int
    reasoning_summary: str
    risk_assessment: str | None
    requested_quantity: str | None
    resulting_order_id: str | None
    execution_outcome: DecisionExecutionOutcome

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("decision timestamp must be timezone-aware")
        for name in (
            "decision_id",
            "strategy_id",
            "symbol",
            "action",
            "reasoning_summary",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty text")
        if not 0 <= self.confidence <= 100:
            raise ValueError("confidence must be between 0 and 100")
        for name in (
            "risk_assessment",
            "requested_quantity",
            "resulting_order_id",
        ):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"{name} must be None or non-empty text")
        if not isinstance(
            self.execution_outcome,
            DecisionExecutionOutcome,
        ):
            raise TypeError(
                "execution_outcome must be a DecisionExecutionOutcome"
            )


@dataclass(frozen=True, slots=True)
class DecisionsReadModelSnapshot:
    decisions: tuple[DecisionRecord, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.decisions, tuple):
            raise TypeError("decisions must be an immutable tuple")
        if any(
            not isinstance(item, DecisionRecord)
            for item in self.decisions
        ):
            raise TypeError("decisions must contain DecisionRecord instances")

    @classmethod
    def initial(cls) -> "DecisionsReadModelSnapshot":
        return cls()
