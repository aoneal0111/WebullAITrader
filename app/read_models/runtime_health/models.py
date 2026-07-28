from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class OverallHealth(StrEnum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


@dataclass(frozen=True, slots=True)
class SubsystemHealth:
    name: str
    health: OverallHealth
    status: str
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in ("name", "status"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be empty")
            if value != value.strip():
                raise ValueError(f"{field_name} must be stripped")
        if not isinstance(self.health, OverallHealth):
            raise TypeError("health must be an OverallHealth")
        _validate_optional_timestamp(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class HealthMetric:
    name: str
    value: int
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name.strip()
            or self.name != self.name.strip()
        ):
            raise ValueError("name must be stripped non-empty text")
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, int)
            or self.value < 0
        ):
            raise ValueError("value must be a nonnegative integer")
        _validate_optional_timestamp(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class RuntimeHealthSnapshot:
    overall_health: OverallHealth
    runtime_state: str
    broker: SubsystemHealth
    scanner: SubsystemHealth
    market_data: SubsystemHealth
    operations_bus: SubsystemHealth
    current_cycle: HealthMetric
    last_completed_cycle: HealthMetric
    last_update_time: datetime | None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.overall_health, OverallHealth):
            raise TypeError("overall_health must be an OverallHealth")
        if (
            not isinstance(self.runtime_state, str)
            or not self.runtime_state.strip()
            or self.runtime_state != self.runtime_state.strip()
        ):
            raise ValueError("runtime_state must be stripped non-empty text")
        expected_subsystems = (
            ("broker", self.broker),
            ("scanner", self.scanner),
            ("market_data", self.market_data),
            ("operations_bus", self.operations_bus),
        )
        for expected_name, subsystem in expected_subsystems:
            if not isinstance(subsystem, SubsystemHealth):
                raise TypeError(f"{expected_name} must be a SubsystemHealth")
            if subsystem.name != expected_name:
                raise ValueError(
                    f"{expected_name} subsystem name must be {expected_name!r}"
                )
        expected_metrics = (
            ("current_cycle", self.current_cycle),
            ("last_completed_cycle", self.last_completed_cycle),
        )
        for expected_name, metric in expected_metrics:
            if not isinstance(metric, HealthMetric):
                raise TypeError(f"{expected_name} must be a HealthMetric")
            if metric.name != expected_name:
                raise ValueError(
                    f"{expected_name} metric name must be {expected_name!r}"
                )
        if self.current_cycle.value < self.last_completed_cycle.value:
            raise ValueError(
                "current_cycle cannot be below last_completed_cycle"
            )
        _validate_optional_timestamp(
            self.last_update_time,
            "last_update_time",
        )
        _validate_messages(self.warnings, "warnings")
        _validate_messages(self.errors, "errors")

    @classmethod
    def initial(cls) -> "RuntimeHealthSnapshot":
        return cls(
            overall_health=OverallHealth.UNKNOWN,
            runtime_state="STOPPED",
            broker=_initial_subsystem("broker"),
            scanner=_initial_subsystem("scanner"),
            market_data=_initial_subsystem("market_data"),
            operations_bus=_initial_subsystem("operations_bus"),
            current_cycle=HealthMetric("current_cycle", 0),
            last_completed_cycle=HealthMetric("last_completed_cycle", 0),
            last_update_time=None,
        )


def _initial_subsystem(name: str) -> SubsystemHealth:
    return SubsystemHealth(
        name=name,
        health=OverallHealth.UNKNOWN,
        status="Unknown",
    )


def _validate_optional_timestamp(
    value: datetime | None,
    field_name: str,
) -> None:
    if value is not None and (
        not isinstance(value, datetime)
        or value.tzinfo is None
    ):
        raise ValueError(f"{field_name} must be timezone-aware or None")


def _validate_messages(messages: tuple[str, ...], field_name: str) -> None:
    if not isinstance(messages, tuple):
        raise TypeError(f"{field_name} must be an immutable tuple")
    if any(
        not isinstance(message, str)
        or not message.strip()
        or message != message.strip()
        for message in messages
    ):
        raise ValueError(
            f"{field_name} must contain stripped non-empty strings"
        )
