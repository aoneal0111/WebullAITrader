from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DecisionRow:
    timestamp: datetime
    strategy: str
    symbol: str
    action: str
    confidence: str
    reasoning: str
    risk: str
    quantity: str
    order_id: str
    outcome: str


@dataclass(frozen=True, slots=True)
class DecisionsSnapshot:
    rows: tuple[DecisionRow, ...] = ()
