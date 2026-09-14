"""Small protocols for the observation-only DI-2 runtime seam."""

from __future__ import annotations

from typing import Protocol


class DecisionIntelligenceObserver(Protocol):
    def start(self, environment: str | None = None) -> None: ...

    def observe_decision(self, *, value: object, candidate: object,
                         signal: object | None = None,
                         taxonomy_candidate: object | None = None,
                         legacy_candidate: object | None = None) -> object | None: ...

    def close(self, *, timeout_seconds: float = 5.0) -> bool: ...
