from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Event

from app.operations.runtime import PaperRuntimeEvent
from app.replay_workspace import (
    ReplayWorkspace,
    ReplayWorkspacePhase,
)
from app.runtime_event_replay import (
    ImmediateReplaySpeed,
    ReplayEngine,
)


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


def events() -> tuple[PaperRuntimeEvent, ...]:
    return tuple(
        PaperRuntimeEvent(
            sequence=index,
            timestamp=NOW + timedelta(seconds=index),
            event_type=event_type,
            message=event_type,
            cycle=1,
        )
        for index, event_type in enumerate(
            (
                "STARTED",
                "BROKER_CONNECTED",
                "MARKET_DATA_CONNECTED",
                "AI_READY",
                "HEARTBEAT",
                "STOPPED",
            ),
            start=1,
        )
    )


def immediate_pacer(speed, control, wake_event):
    del speed, control, wake_event
    return ImmediateReplaySpeed()


class BlockingPacerFactory:
    def __init__(self) -> None:
        self.blocked = Event()
        self._has_blocked = False

    def __call__(self, speed, control, wake_event):
        del speed
        blocked = self.blocked

        class Pacer:
            def pace(self, previous_event, next_event) -> None:
                del next_event
                if previous_event is not None and not factory._has_blocked:
                    factory._has_blocked = True
                    blocked.set()
                    wake_event.wait(5)
                    wake_event.clear()

        factory = self
        return Pacer()


def test_play_completes_and_publishes_normal_projection_state() -> None:
    recorded = events()
    workspace = ReplayWorkspace(
        ReplayEngine(recorded),
        pacer_factory=immediate_pacer,
    )

    assert workspace.play() is True
    assert workspace.wait(1) is True

    state = workspace.state
    assert state.replay.phase is ReplayWorkspacePhase.COMPLETED
    assert state.replay.current_event == len(recorded)
    assert state.replay.events_processed == len(recorded)
    assert state.health_projection.runtime_status == "STOPPED"
    assert state.health_projection.last_heartbeat == NOW + timedelta(seconds=5)
    expected = ReplayEngine(recorded).replay().state
    assert state.order_projection == expected.order_projection
    assert state.position_projection == expected.position_projection
    assert state.timeline_projection == expected.timeline_projection
    assert state.decision_projection == expected.decision_projection
    assert state.portfolio_projection == expected.portfolio_projection
    assert state.health_projection == expected.health_projection
    assert state.watchlist_projection == expected.watchlist_projection
    assert state == workspace.state


def test_pause_interrupts_playback_without_losing_projected_state() -> None:
    pacer_factory = BlockingPacerFactory()
    workspace = ReplayWorkspace(
        ReplayEngine(events()),
        pacer_factory=pacer_factory,
    )

    workspace.play()
    assert pacer_factory.blocked.wait(1)
    assert workspace.pause() is True
    assert workspace.wait(1)

    state = workspace.state
    assert state.replay.phase is ReplayWorkspacePhase.PAUSED
    assert state.replay.current_event == 1
    assert state.health_projection.runtime_status == "RUNNING"

    assert workspace.play() is True
    assert workspace.wait(1)
    assert workspace.state.replay.phase is ReplayWorkspacePhase.COMPLETED


def test_step_advances_exactly_one_event() -> None:
    workspace = ReplayWorkspace(ReplayEngine(events()))

    assert workspace.step() is True

    state = workspace.state
    assert state.replay.phase is ReplayWorkspacePhase.PAUSED
    assert state.replay.current_event == 1
    assert state.replay.events_processed == 1
    assert state.health_projection.runtime_status == "RUNNING"


def test_restart_resets_replay_and_every_projection() -> None:
    workspace = ReplayWorkspace(ReplayEngine(events()))
    workspace.step()

    workspace.restart()

    state = workspace.state
    assert state.replay.phase is ReplayWorkspacePhase.READY
    assert state.replay.current_event == 0
    assert state.replay.elapsed_seconds == 0
    assert state.health_projection.runtime_status is None
    assert state.timeline_projection.entries == ()


def test_jump_to_event_index_rebuilds_prefix_state() -> None:
    workspace = ReplayWorkspace(ReplayEngine(events()))

    workspace.jump_to_event_index(2)

    state = workspace.state
    assert state.replay.current_event == 2
    assert state.replay.phase is ReplayWorkspacePhase.PAUSED
    assert state.health_projection.runtime_status == "RUNNING"
    assert state.health_projection.broker_status == "CONNECTED"
    assert state.health_projection.market_data_status is None


def test_jump_to_timestamp_is_inclusive() -> None:
    workspace = ReplayWorkspace(ReplayEngine(events()))

    workspace.jump_to_timestamp(NOW + timedelta(seconds=3))

    state = workspace.state
    assert state.replay.current_event == 3
    assert state.health_projection.broker_status == "CONNECTED"
    assert state.health_projection.market_data_status == "CONNECTED"
    assert state.health_projection.ai_status is None


def test_completion_disables_further_steps() -> None:
    workspace = ReplayWorkspace(ReplayEngine(events()[:1]))

    assert workspace.step() is True
    assert workspace.step() is False
    assert workspace.state.replay.phase is ReplayWorkspacePhase.COMPLETED


def test_cancellation_stops_active_playback() -> None:
    pacer_factory = BlockingPacerFactory()
    workspace = ReplayWorkspace(
        ReplayEngine(events()),
        pacer_factory=pacer_factory,
    )

    workspace.play()
    assert pacer_factory.blocked.wait(1)
    assert workspace.cancel() is True
    assert workspace.wait(1)

    assert workspace.state.replay.phase is ReplayWorkspacePhase.CANCELLED
    assert workspace.state.replay.current_event == 1


def test_workspace_notifies_with_immutable_application_state() -> None:
    workspace = ReplayWorkspace(ReplayEngine(events()))
    observed = []
    listener_id = workspace.subscribe(observed.append)

    workspace.step()

    assert observed[0].replay.phase is ReplayWorkspacePhase.READY
    assert observed[-1] == workspace.state
    assert workspace.unsubscribe(listener_id) is True
