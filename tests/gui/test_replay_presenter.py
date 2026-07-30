from app.gui.models import ReplayWorkspaceSnapshot
from app.gui.presenters import ReplayPresenter
from app.operations_core import ApplicationState
from app.replay_workspace import ReplayWorkspacePhase, ReplayWorkspaceState


class View:
    def __init__(self) -> None:
        self.snapshot = None

    def render(self, snapshot: ReplayWorkspaceSnapshot) -> None:
        self.snapshot = snapshot


def test_replay_presenter_formats_immutable_workspace_model() -> None:
    view = View()
    presenter = ReplayPresenter(view)
    state = ApplicationState(
        replay=ReplayWorkspaceState(
            phase=ReplayWorkspacePhase.PAUSED,
            current_event=1250,
            events_processed=1250,
            total_events=5000,
            replay_speed=2.5,
            elapsed_seconds=65.25,
        )
    )

    presenter.render(state)

    assert view.snapshot == ReplayWorkspaceSnapshot(
        status="Paused",
        current_position="1,250 / 5,000",
        events_processed="1,250",
        total_events="5,000",
        replay_speed="2.5\u00d7",
        elapsed_time="00:01:05.250",
        maximum_event_index=5000,
        can_play=True,
        can_pause=False,
        can_step=True,
        can_restart=True,
        can_seek=True,
    )


def test_replay_presenter_formats_playing_controls() -> None:
    view = View()
    presenter = ReplayPresenter(view)

    presenter.render(
        ApplicationState(
            replay=ReplayWorkspaceState(
                phase=ReplayWorkspacePhase.PLAYING,
                current_event=1,
                events_processed=1,
                total_events=2,
            )
        )
    )

    assert view.snapshot.can_play is False
    assert view.snapshot.can_pause is True
    assert view.snapshot.can_step is False
    assert view.snapshot.can_seek is False


def test_replay_presenter_renders_workspace_and_dashboard_from_one_model() -> None:
    workspace_view = View()
    dashboard_view = View()
    presenter = ReplayPresenter(workspace_view, dashboard_view)
    state = ApplicationState(
        replay=ReplayWorkspaceState(
            phase=ReplayWorkspacePhase.PAUSED,
            current_event=3,
            events_processed=3,
            total_events=10,
        )
    )

    presenter.render(state)

    assert workspace_view.snapshot is dashboard_view.snapshot
    assert dashboard_view.snapshot.current_position == "3 / 10"
