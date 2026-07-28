import pytest

from app.event_store import EventStoreSnapshot
from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import ApplicationState


def test_dashboard_carries_event_store_snapshot() -> None:
    snapshot = EventStoreSnapshot.initial()

    assert project_dashboard(
        ApplicationState(),
        event_store=snapshot,
    ).event_store is snapshot


def test_dashboard_rejects_wrong_event_store_snapshot() -> None:
    with pytest.raises(TypeError, match="EventStoreSnapshot"):
        project_dashboard(
            ApplicationState(),
            event_store=object(),  # type: ignore[arg-type]
        )
