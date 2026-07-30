from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.operations_core import ApplicationState


class ReplayStatus(StrEnum):
    EMPTY = "EMPTY"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    INTERRUPTED = "INTERRUPTED"


@dataclass(frozen=True, slots=True)
class ReplayProgress:
    current_event: int
    total_events: int

    def __post_init__(self) -> None:
        for field_name in ("current_event", "total_events"):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{field_name} must be nonnegative")
        if self.current_event > self.total_events:
            raise ValueError("current_event cannot exceed total_events")


@dataclass(frozen=True, slots=True)
class ReplayStatistics:
    events_processed: int
    elapsed_seconds: float
    processing_rate: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.events_processed, bool)
            or not isinstance(self.events_processed, int)
            or self.events_processed < 0
        ):
            raise ValueError("events_processed must be nonnegative")
        for field_name in ("elapsed_seconds", "processing_rate"):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
            ):
                raise ValueError(f"{field_name} must be nonnegative")


@dataclass(frozen=True, slots=True)
class ReplayResult:
    status: ReplayStatus
    start_index: int
    next_index: int
    processed_events: int
    total_events: int
    state: ApplicationState
    progress: ReplayProgress
    statistics: ReplayStatistics

    def __post_init__(self) -> None:
        for field_name in (
            "start_index",
            "next_index",
            "processed_events",
            "total_events",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{field_name} must be nonnegative")
        if self.start_index > self.total_events:
            raise ValueError("start_index cannot exceed total_events")
        if not self.start_index <= self.next_index <= self.total_events:
            raise ValueError("next_index is outside the replay range")
        if self.processed_events != self.next_index - self.start_index:
            raise ValueError("processed_events must match the replay range")
        if self.progress != ReplayProgress(
            current_event=self.next_index,
            total_events=self.total_events,
        ):
            raise ValueError("progress must match the replay range")
        if self.statistics.events_processed != self.processed_events:
            raise ValueError("statistics must match processed_events")

    @property
    def interrupted(self) -> bool:
        return self.status is ReplayStatus.INTERRUPTED

    @property
    def completed(self) -> bool:
        return self.status in {
            ReplayStatus.EMPTY,
            ReplayStatus.COMPLETED,
        }
