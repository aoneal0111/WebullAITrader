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
class ReplayResult:
    status: ReplayStatus
    start_index: int
    next_index: int
    processed_events: int
    total_events: int
    state: ApplicationState

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

    @property
    def interrupted(self) -> bool:
        return self.status is ReplayStatus.INTERRUPTED

    @property
    def completed(self) -> bool:
        return self.status in {
            ReplayStatus.EMPTY,
            ReplayStatus.COMPLETED,
        }
