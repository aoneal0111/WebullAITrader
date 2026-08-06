from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MissionStatusRow:
    label: str
    value: str
    tone: str = "neutral"


@dataclass(frozen=True, slots=True)
class MissionStatusSnapshot:
    rows: tuple[MissionStatusRow, ...] = ()

    @classmethod
    def initial(cls) -> "MissionStatusSnapshot":
        return cls()


@dataclass(frozen=True, slots=True)
class AIThinkingSnapshot:
    state: str = "Waiting for next scan"
    detail: str = "Runtime reasoning is unavailable."
    reasoning: str = "Unknown"
    last_decision: str = "Unknown"
    tone: str = "neutral"

    @classmethod
    def initial(cls) -> "AIThinkingSnapshot":
        return cls()


__all__ = [
    "AIThinkingSnapshot",
    "MissionStatusRow",
    "MissionStatusSnapshot",
]
