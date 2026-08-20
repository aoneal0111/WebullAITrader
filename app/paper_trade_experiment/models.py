"""Immutable feature, execution, and label records for paper experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping


class ExecutionState(StrEnum):
    NOT_EXECUTED = "NOT_EXECUTED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """One canonical opportunity with features and future labels separated."""

    candidate_id: str
    trade_id: str | None
    features: Mapping[str, Any]
    labels: Mapping[str, Any] = field(default_factory=dict)
    execution: Mapping[str, Any] = field(default_factory=dict)

    @property
    def symbol(self) -> str:
        return str(self.features["symbol"])

    @property
    def paper_trade_executed(self) -> bool:
        return bool(self.execution.get("paper_trade_executed", False))


@dataclass(frozen=True, slots=True)
class PriceObservation:
    symbol: str
    timestamp: datetime
    price: Decimal


HORIZONS_SECONDS: Mapping[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
}


COHORTS = (
    "A_STRICT_CATALYST",
    "B_TECHNICAL_ONLY",
    "C_NO_CATALYST",
    "D_CORROBORATED_CATALYST",
    "E_STRONG_PRIMARY_CATALYST",
)


__all__ = [
    "COHORTS",
    "CandidateRecord",
    "ExecutionState",
    "HORIZONS_SECONDS",
    "PriceObservation",
]
