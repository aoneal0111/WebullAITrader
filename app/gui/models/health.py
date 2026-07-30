from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HealthDashboardSnapshot:
    overall_status: str
    status_level: str
    metrics: tuple[tuple[str, str], ...]
    incident: str

    @classmethod
    def initial(cls) -> "HealthDashboardSnapshot":
        return cls(
            overall_status="UNKNOWN",
            status_level="warn",
            metrics=(),
            incident="No infrastructure health events received.",
        )
