from __future__ import annotations

from app.gui.models.replay import ReplayWorkspaceSnapshot
from app.replay_workspace import ReplayWorkspacePhase, ReplayWorkspaceState


def format_replay(
    state: ReplayWorkspaceState,
) -> ReplayWorkspaceSnapshot:
    if not isinstance(state, ReplayWorkspaceState):
        raise TypeError("state must be a ReplayWorkspaceState")
    can_advance = state.current_event < state.total_events
    idle = state.phase is not ReplayWorkspacePhase.PLAYING
    return ReplayWorkspaceSnapshot(
        status=state.phase.value.title(),
        current_position=f"{state.current_event:,} / {state.total_events:,}",
        events_processed=f"{state.events_processed:,}",
        total_events=f"{state.total_events:,}",
        replay_speed=f"{state.replay_speed:g}\u00d7",
        elapsed_time=_format_elapsed(state.elapsed_seconds),
        maximum_event_index=state.total_events,
        can_play=idle and can_advance,
        can_pause=not idle,
        can_step=idle and can_advance,
        can_restart=idle and (
            state.current_event > 0
            or state.phase
            in {
                ReplayWorkspacePhase.CANCELLED,
                ReplayWorkspacePhase.COMPLETED,
            }
        ),
        can_seek=idle and state.total_events > 0,
    )


def _format_elapsed(elapsed_seconds: float) -> str:
    total_milliseconds = round(elapsed_seconds * 1000)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return (
        f"{hours:02}:{minutes:02}:{seconds:02}."
        f"{milliseconds:03}"
    )


__all__ = ["format_replay"]
