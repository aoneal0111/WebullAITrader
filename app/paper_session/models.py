from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.execution_coordinator import ExecutionCoordinationResult
from app.paper_trading.models import (
    EquityPoint,
    PaperJournal,
    PaperPortfolio,
    PerformanceMetrics,
)


ZERO = Decimal("0")


class PaperSessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class PaperSessionStatistics:
    decisions_processed: int
    orders_attempted: int
    orders_filled: int
    orders_rejected: int
    orders_not_filled: int
    decisions_skipped: int
    winning_fills: int
    losing_fills: int
    breakeven_fills: int
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    current_equity: Decimal
    peak_equity: Decimal
    current_drawdown: Decimal

    def __post_init__(self) -> None:
        integer_values = (
            self.decisions_processed,
            self.orders_attempted,
            self.orders_filled,
            self.orders_rejected,
            self.orders_not_filled,
            self.decisions_skipped,
            self.winning_fills,
            self.losing_fills,
            self.breakeven_fills,
        )

        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in integer_values
        ):
            raise ValueError(
                "session statistic counts must be "
                "nonnegative integers"
            )

        decimal_values = (
            self.realized_pnl,
            self.unrealized_pnl,
            self.current_equity,
            self.peak_equity,
            self.current_drawdown,
        )

        if any(
            not isinstance(value, Decimal)
            or not value.is_finite()
            for value in decimal_values
        ):
            raise ValueError(
                "session financial statistics must be "
                "finite Decimals"
            )

        if self.current_equity < ZERO:
            raise ValueError("current equity cannot be negative")

        if self.peak_equity < self.current_equity:
            raise ValueError(
                "peak equity cannot be below current equity"
            )

        if self.current_drawdown < ZERO:
            raise ValueError("drawdown cannot be negative")


@dataclass(frozen=True, slots=True)
class PaperSessionEvent:
    sequence: int
    timestamp: datetime
    request_id: str | None
    status: str
    final_stage: str
    message: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError(
                "session event sequence must be positive"
            )

        if self.timestamp.tzinfo is None:
            raise ValueError(
                "session event timestamp must be timezone-aware"
            )

        if self.request_id is not None:
            normalized = self.request_id.strip()

            if not normalized:
                raise ValueError(
                    "request ID cannot be blank"
                )

            object.__setattr__(
                self,
                "request_id",
                normalized,
            )

        if not self.status.strip():
            raise ValueError("event status is required")

        if not self.final_stage.strip():
            raise ValueError("event final stage is required")

        if not self.message.strip():
            raise ValueError("event message is required")


@dataclass(frozen=True, slots=True)
class ProcessDecisionResult:
    session: PaperTradingSession
    coordination: ExecutionCoordinationResult


@dataclass(frozen=True, slots=True)
class PaperTradingSession:
    schema_version: str
    session_id: str
    status: PaperSessionStatus
    started_at: datetime
    ended_at: datetime | None
    portfolio: PaperPortfolio
    journal: PaperJournal
    equity_curve: tuple[EquityPoint, ...]
    metrics: PerformanceMetrics
    statistics: PaperSessionStatistics
    processed_request_ids: tuple[str, ...]
    events: tuple[PaperSessionEvent, ...]
    last_coordination_result: (
        ExecutionCoordinationResult | None
    )

    def __post_init__(self) -> None:
        if not self.schema_version.strip():
            raise ValueError("schema version is required")

        session_id = self.session_id.strip()

        if not session_id:
            raise ValueError("session ID is required")

        object.__setattr__(self, "session_id", session_id)

        if self.started_at.tzinfo is None:
            raise ValueError(
                "session start must be timezone-aware"
            )

        if self.ended_at is not None:
            if self.ended_at.tzinfo is None:
                raise ValueError(
                    "session end must be timezone-aware"
                )

            if self.ended_at < self.started_at:
                raise ValueError(
                    "session cannot end before it starts"
                )

        if (
            self.status is PaperSessionStatus.ACTIVE
            and self.ended_at is not None
        ):
            raise ValueError(
                "active session cannot have an end time"
            )

        if (
            self.status is PaperSessionStatus.CLOSED
            and self.ended_at is None
        ):
            raise ValueError(
                "closed session requires an end time"
            )

        if not self.equity_curve:
            raise ValueError(
                "session equity curve cannot be empty"
            )

        if (
            self.equity_curve[-1].equity
            != self.portfolio.equity
        ):
            raise ValueError(
                "equity curve must end at portfolio equity"
            )

        normalized_ids = tuple(
            item.strip()
            for item in self.processed_request_ids
        )

        if any(not item for item in normalized_ids):
            raise ValueError(
                "processed request IDs cannot be blank"
            )

        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError(
                "processed request IDs must be unique"
            )

        object.__setattr__(
            self,
            "processed_request_ids",
            normalized_ids,
        )

