from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DecisionReadModel:
    """One immutable, presentation-safe autonomous strategy decision."""

    symbol: str
    action: str
    confidence: int
    score: Decimal
    reasons: tuple[str, ...]
    source_action: str
    position_quantity: Decimal
    strategy_version: str
    decided_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "symbol",
            "action",
            "source_action",
            "strategy_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
            if value != value.strip():
                raise ValueError(f"{field_name} must be stripped")
        if self.symbol != self.symbol.upper():
            raise ValueError("symbol must be uppercase")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, int)
            or not 0 <= self.confidence <= 100
        ):
            raise ValueError("confidence must be an integer between 0 and 100")
        for field_name in ("score", "position_quantity"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field_name} must be a finite Decimal")
        if not isinstance(self.reasons, tuple):
            raise TypeError("reasons must be an immutable tuple")
        if any(
            not isinstance(reason, str)
            or not reason.strip()
            or reason != reason.strip()
            for reason in self.reasons
        ):
            raise ValueError("reasons must contain stripped non-empty strings")
        if (
            not isinstance(self.decided_at, datetime)
            or self.decided_at.tzinfo is None
        ):
            raise ValueError("decided_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class DecisionsReadModelSnapshot:
    """Latest immutable decision batch projected from OperationsBus events."""

    cycle: int | None = None
    updated_at: datetime | None = None
    decisions: tuple[DecisionReadModel, ...] = ()

    def __post_init__(self) -> None:
        if self.cycle is not None and (
            isinstance(self.cycle, bool)
            or not isinstance(self.cycle, int)
            or self.cycle < 1
        ):
            raise ValueError("cycle must be a positive integer or None")
        if self.updated_at is not None and (
            not isinstance(self.updated_at, datetime)
            or self.updated_at.tzinfo is None
        ):
            raise ValueError("updated_at must be timezone-aware or None")
        if (self.cycle is None) != (self.updated_at is None):
            raise ValueError(
                "cycle and updated_at must either both be set or both be None"
            )
        if not isinstance(self.decisions, tuple):
            raise TypeError("decisions must be an immutable tuple")
        if any(
            not isinstance(decision, DecisionReadModel)
            for decision in self.decisions
        ):
            raise TypeError(
                "decisions must contain only DecisionReadModel instances"
            )
        if self.cycle is None and self.decisions:
            raise ValueError("initial snapshot cannot contain decisions")

    @classmethod
    def initial(cls) -> "DecisionsReadModelSnapshot":
        return cls()
