import pytest

from app.backtesting.models import ExperimentSnapshot
from app.gui.models import DashboardSnapshot
from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import ApplicationState


def test_dashboard_carries_experiment_snapshot() -> None:
    snapshot = ExperimentSnapshot.initial()
    assert project_dashboard(
        ApplicationState(),
        experiments=snapshot,
    ).experiments is snapshot
    assert DashboardSnapshot.initial().experiments == snapshot


def test_dashboard_rejects_wrong_experiment_snapshot() -> None:
    with pytest.raises(TypeError, match="ExperimentSnapshot"):
        project_dashboard(ApplicationState(), experiments=object())
