from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReplayWorkspaceSnapshot:
    status: str
    current_position: str
    events_processed: str
    total_events: str
    replay_speed: str
    elapsed_time: str
    maximum_event_index: int
    can_play: bool
    can_pause: bool
    can_step: bool
    can_restart: bool
    can_seek: bool


__all__ = ["ReplayWorkspaceSnapshot"]
