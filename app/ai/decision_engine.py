from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping


class DecisionValidationError(ValueError):
    """Raised when an AI decision does not match the safe output schema."""


class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True, slots=True)
class TradingDecision:
    """Analysis output only; this object cannot submit or manage orders."""

    action: Action
    confidence: int
    reason: str
    stop_loss: Decimal | None
    take_profit: Decimal | None

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 100:
            raise DecisionValidationError("confidence must be between 0 and 100")
        if not self.reason.strip():
            raise DecisionValidationError("reason must not be empty")
        for name, value in (
            ("stop_loss", self.stop_loss),
            ("take_profit", self.take_profit),
        ):
            if value is not None and value <= 0:
                raise DecisionValidationError(f"{name} must be greater than zero")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TradingDecision":
        try:
            action = Action(str(value["action"]).upper())
            confidence = int(value["confidence"])
            reason = str(value["reason"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DecisionValidationError("invalid action, confidence, or reason") from exc

        return cls(
            action=action,
            confidence=confidence,
            reason=reason,
            stop_loss=_optional_decimal(value.get("stop_loss"), "stop_loss"),
            take_profit=_optional_decimal(value.get("take_profit"), "take_profit"),
        )

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "action": self.action.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "stop_loss": format(self.stop_loss, "f") if self.stop_loss is not None else None,
            "take_profit": format(self.take_profit, "f") if self.take_profit is not None else None,
        }


def _optional_decimal(value: Any, field_name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DecisionValidationError(f"{field_name} must be numeric or null") from exc
