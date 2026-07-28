from datetime import datetime, timezone

import pytest

from app.gui.models import HealthCenterSnapshot
from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import ApplicationState
from app.read_models.runtime_health import (
    HealthMetric,
    OverallHealth,
    RuntimeHealthSnapshot,
    SubsystemHealth,
)


NOW = datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc)


def subsystem(
    name: str,
    health: OverallHealth,
    status: str,
) -> SubsystemHealth:
    return SubsystemHealth(name, health, status, NOW)


def health_snapshot() -> RuntimeHealthSnapshot:
    return RuntimeHealthSnapshot(
        overall_health=OverallHealth.DEGRADED,
        runtime_state="STOPPING",
        broker=subsystem(
            "broker",
            OverallHealth.DEGRADED,
            "Disconnecting",
        ),
        scanner=subsystem(
            "scanner",
            OverallHealth.DEGRADED,
            "Stopping",
        ),
        market_data=subsystem(
            "market_data",
            OverallHealth.UNHEALTHY,
            "Error",
        ),
        operations_bus=subsystem(
            "operations_bus",
            OverallHealth.HEALTHY,
            "Receiving events",
        ),
        current_cycle=HealthMetric("current_cycle", 7, NOW),
        last_completed_cycle=HealthMetric(
            "last_completed_cycle",
            7,
            NOW,
        ),
        last_update_time=NOW,
        warnings=("Shutdown in progress.",),
        errors=("Market feed failed.",),
    )


def test_dashboard_formats_runtime_health_as_badges() -> None:
    dashboard = project_dashboard(
        ApplicationState(),
        runtime_health=health_snapshot(),
    )

    health = dashboard.runtime_health
    assert health.overall_health.value == "DEGRADED"
    assert health.overall_health.level == "warn"
    assert health.runtime_state.value == "STOPPING"
    assert health.market_data_status.level == "danger"
    assert health.operations_bus_status.level == "good"
    assert health.current_cycle.value == "7"
    assert health.last_completed_cycle.value == "7"
    assert health.last_update_time.value != "NEVER"
    assert health.warnings[0].value == "Shutdown in progress."
    assert health.errors[0].value == "Market feed failed."


def test_dashboard_defaults_to_initial_health_center() -> None:
    assert project_dashboard(ApplicationState()).runtime_health == (
        HealthCenterSnapshot.initial()
    )


def test_dashboard_rejects_wrong_runtime_health_model() -> None:
    with pytest.raises(TypeError, match="RuntimeHealthSnapshot"):
        project_dashboard(
            ApplicationState(),
            runtime_health=object(),  # type: ignore[arg-type]
        )
