from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Advisory risk result; it cannot create or submit brokerage orders."""

    approved: bool
    approval_reason: str
    risk_score: int
    max_position_percent: Decimal
    stop_loss_valid: bool
    take_profit_valid: bool
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 0 <= self.risk_score <= 100:
            raise ValueError("risk_score must be between 0 and 100")
        if not isinstance(self.max_position_percent, Decimal):
            object.__setattr__(self, "max_position_percent", Decimal(str(self.max_position_percent)))
        if not 0 <= self.max_position_percent <= 100:
            raise ValueError("max_position_percent must be between 0 and 100")
        if not self.approval_reason.strip():
            raise ValueError("approval_reason must not be empty")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["max_position_percent"] = format(self.max_position_percent, "f")
        value["warnings"] = list(self.warnings)
        return value
