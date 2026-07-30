from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class ReplayWorkspacePhase(StrEnum):
    READY = "READY"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ReplayWorkspaceState:
    phase: ReplayWorkspacePhase = ReplayWorkspacePhase.READY
    current_event: int = 0
    events_processed: int = 0
    total_events: int = 0
    replay_speed: float = 1.0
    elapsed_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.phase, ReplayWorkspacePhase):
            raise TypeError("phase must be a ReplayWorkspacePhase")
        for field_name in (
            "current_event",
            "events_processed",
            "total_events",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{field_name} must be nonnegative")
        if self.current_event > self.total_events:
            raise ValueError("current_event cannot exceed total_events")
        if self.events_processed > self.total_events:
            raise ValueError("events_processed cannot exceed total_events")
        for field_name in ("replay_speed", "elapsed_seconds"):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError(f"{field_name} must be finite")
        if self.replay_speed <= 0:
            raise ValueError("replay_speed must be positive")
        if self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be nonnegative")

    @property
    def completed(self) -> bool:
        return self.phase is ReplayWorkspacePhase.COMPLETED

    @property
    def active(self) -> bool:
        return self.phase is ReplayWorkspacePhase.PLAYING


__all__ = ["ReplayWorkspacePhase", "ReplayWorkspaceState"]
