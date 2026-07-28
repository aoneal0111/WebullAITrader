from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from app.read_models.runtime_health import (
    HealthMetric,
    OverallHealth,
    RuntimeHealthSnapshot,
    SubsystemHealth,
)


NOW = datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc)


def subsystem(
    name: str,
    health: OverallHealth = OverallHealth.HEALTHY,
) -> SubsystemHealth:
    return SubsystemHealth(name, health, "Ready", NOW)


def make_snapshot(**changes) -> RuntimeHealthSnapshot:
    values = {
        "overall_health": OverallHealth.HEALTHY,
        "runtime_state": "RUNNING",
        "broker": subsystem("broker"),
        "scanner": subsystem("scanner"),
        "market_data": subsystem("market_data"),
        "operations_bus": subsystem("operations_bus"),
        "current_cycle": HealthMetric("current_cycle", 3, NOW),
        "last_completed_cycle": HealthMetric(
            "last_completed_cycle",
            2,
            NOW,
        ),
        "last_update_time": NOW,
        "warnings": (),
        "errors": (),
    }
    values.update(changes)
    return RuntimeHealthSnapshot(**values)


def test_initial_snapshot_is_safe_unknown_and_immutable() -> None:
    snapshot = RuntimeHealthSnapshot.initial()

    assert snapshot.overall_health is OverallHealth.UNKNOWN
    assert snapshot.runtime_state == "STOPPED"
    assert snapshot.current_cycle.value == 0
    assert snapshot.last_update_time is None
    with pytest.raises(FrozenInstanceError):
        snapshot.runtime_state = "RUNNING"  # type: ignore[misc]


def test_subsystem_and_metric_are_frozen_and_slotted() -> None:
    health = subsystem("broker")
    metric = HealthMetric("current_cycle", 1, NOW)

    with pytest.raises(FrozenInstanceError):
        health.status = "Changed"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        metric.extra = 1  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("factory", "message"),
    (
        (
            lambda: SubsystemHealth(
                " broker",
                OverallHealth.HEALTHY,
                "Ready",
            ),
            "name",
        ),
        (
            lambda: SubsystemHealth(  # type: ignore[arg-type]
                "broker",
                "HEALTHY",
                "Ready",
            ),
            "OverallHealth",
        ),
        (
            lambda: HealthMetric("current_cycle", -1),
            "nonnegative",
        ),
        (
            lambda: HealthMetric(
                "current_cycle",
                1,
                NOW.replace(tzinfo=None),
            ),
            "timezone-aware",
        ),
    ),
)
def test_component_models_validate(factory, message) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        factory()


def test_snapshot_requires_canonical_component_names() -> None:
    with pytest.raises(ValueError, match="broker subsystem name"):
        make_snapshot(broker=subsystem("scanner"))


def test_snapshot_rejects_cycle_regression() -> None:
    with pytest.raises(ValueError, match="cannot be below"):
        make_snapshot(
            current_cycle=HealthMetric("current_cycle", 1, NOW),
            last_completed_cycle=HealthMetric(
                "last_completed_cycle",
                2,
                NOW,
            ),
        )


def test_snapshot_requires_immutable_valid_messages() -> None:
    with pytest.raises(TypeError, match="immutable tuple"):
        make_snapshot(warnings=["warning"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty"):
        make_snapshot(errors=("",))
