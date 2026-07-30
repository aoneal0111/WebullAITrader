from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PortfolioDashboardSnapshot:
    metrics: tuple[tuple[str, str], ...]
    highlights: tuple[tuple[str, str], ...]

    @classmethod
    def initial(cls) -> "PortfolioDashboardSnapshot":
        return cls(metrics=(), highlights=())
