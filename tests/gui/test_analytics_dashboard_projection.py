import pytest

from app.analytics import AnalyticsSnapshot
from app.gui.models import DashboardSnapshot
from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import ApplicationState


def test_dashboard_snapshot_carries_analytics_snapshot() -> None:
    analytics = AnalyticsSnapshot.initial()
    projected = project_dashboard(
        ApplicationState(),
        analytics=analytics,
    )
    assert projected.analytics is analytics
    assert DashboardSnapshot.initial().analytics == analytics


def test_dashboard_projection_rejects_mutable_analytics_input() -> None:
    with pytest.raises(TypeError, match="analytics"):
        project_dashboard(ApplicationState(), analytics={})
