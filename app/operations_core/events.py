from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationsEvent:
    """Base class for immutable Operations Center business events."""

    occurred_at: datetime = field(default_factory=utc_now)
    event_id: UUID = field(default_factory=uuid4)
    source: str = "operations"

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")

        if not self.source.strip():
            raise ValueError("source must not be empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStarting(OperationsEvent):
    environment: str = "PAPER"


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStarted(OperationsEvent):
    environment: str = "PAPER"
    active_model: str = "Not loaded"


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeCycleCompleted(OperationsEvent):
    cycle_count: int

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if self.cycle_count < 0:
            raise ValueError("cycle_count must be nonnegative")


@dataclass(frozen=True, slots=True, kw_only=True)
class ScannerSnapshotUpdated(OperationsEvent):
    ranked_symbols: tuple[str, ...] = ()
    resolved_symbols: tuple[str, ...] = ()
    missing_symbols: tuple[str, ...] = ()
    events_read: int = 0
    decisions_created: int = 0

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if self.events_read < 0:
            raise ValueError("events_read must be nonnegative")

        if self.decisions_created < 0:
            raise ValueError("decisions_created must be nonnegative")

        for collection_name, symbols in (
            ("ranked_symbols", self.ranked_symbols),
            ("resolved_symbols", self.resolved_symbols),
            ("missing_symbols", self.missing_symbols),
        ):
            normalized = tuple(symbol.strip().upper() for symbol in symbols)

            if any(not symbol for symbol in normalized):
                raise ValueError(
                    f"{collection_name} must not contain empty symbols"
                )

            if len(set(normalized)) != len(normalized):
                raise ValueError(
                    f"{collection_name} must not contain duplicates"
                )

            if normalized != symbols:
                raise ValueError(
                    f"{collection_name} symbols must be normalized"
                )


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStopping(OperationsEvent):
    reason: str = "Operator requested shutdown"


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeStopped(OperationsEvent):
    reason: str = "Runtime stopped cleanly"
    cycles_completed: int = 0

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if self.cycles_completed < 0:
            raise ValueError("cycles_completed must be nonnegative")


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeFailed(OperationsEvent):
    error_message: str

    def __post_init__(self) -> None:
        OperationsEvent.__post_init__(self)

        if not self.error_message.strip():
            raise ValueError("error_message must not be empty")
