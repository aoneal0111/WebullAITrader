from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class StrategyDecisionAction(StrEnum):
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    EXIT_LONG = "EXIT_LONG"
    EXIT_SHORT = "EXIT_SHORT"
    HOLD = "HOLD"
    IGNORE = "IGNORE"


@dataclass(frozen=True, slots=True)
class StrategyPosition:
    symbol: str
    quantity: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()

        if not symbol:
            raise ValueError("position symbol is required")

        object.__setattr__(self, "symbol", symbol)

    @property
    def is_flat(self) -> bool:
        return self.quantity == Decimal("0")

    @property
    def is_long(self) -> bool:
        return self.quantity > Decimal("0")

    @property
    def is_short(self) -> bool:
        return self.quantity < Decimal("0")


@dataclass(frozen=True, slots=True)
class StrategyEngineConfig:
    entry_confidence: int = 60
    exit_confidence: int = 50
    allow_short_entries: bool = False
    reverse_positions: bool = False
    cooldown_seconds: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.entry_confidence <= 100:
            raise ValueError(
                "entry_confidence must be between 0 and 100"
            )

        if not 0 <= self.exit_confidence <= 100:
            raise ValueError(
                "exit_confidence must be between 0 and 100"
            )

        if self.cooldown_seconds < 0:
            raise ValueError(
                "cooldown_seconds cannot be negative"
            )


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    symbol: str
    action: StrategyDecisionAction
    confidence: int
    score: Decimal
    timestamp: datetime
    reasons: tuple[str, ...]
    source_action: str
    position_quantity: Decimal
    strategy_version: str = "1.0"

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()

        if not symbol:
            raise ValueError("decision symbol is required")

        if not 0 <= self.confidence <= 100:
            raise ValueError(
                "confidence must be between 0 and 100"
            )

        if (
            self.timestamp.tzinfo is None
            or self.timestamp.utcoffset() is None
        ):
            raise ValueError(
                "timestamp must be timezone-aware"
            )

        if not self.source_action.strip():
            raise ValueError("source_action is required")

        if not self.strategy_version.strip():
            raise ValueError("strategy_version is required")

        object.__setattr__(self, "symbol", symbol)

    @property
    def creates_order_intent(self) -> bool:
        return self.action in {
            StrategyDecisionAction.ENTER_LONG,
            StrategyDecisionAction.ENTER_SHORT,
            StrategyDecisionAction.EXIT_LONG,
            StrategyDecisionAction.EXIT_SHORT,
        }
