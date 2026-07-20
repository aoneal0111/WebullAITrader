from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any


class ResponseAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True, slots=True)
class AIResponse:
    """Validated advisory output; it has no order-execution behavior."""

    action: ResponseAction
    confidence: int
    reason: str
    stop_loss: Decimal | None
    take_profit: Decimal | None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "action": self.action.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "stop_loss": format(self.stop_loss, "f") if self.stop_loss is not None else None,
            "take_profit": format(self.take_profit, "f") if self.take_profit is not None else None,
        }
