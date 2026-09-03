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
    objective: str = "Unknown"
    operational_state: str = "Waiting for the next scan cycle."
    reasoning: str = "Unknown"
    last_decision: str = "Unknown"
    next_evaluation: str = "Unknown"
    confidence: str = "Unknown"
    tone: str = "neutral"

    @property
    def state(self) -> str:
        """Compatibility alias for the former compact panel model."""

        return self.operational_state

    @property
    def detail(self) -> str:
        """Compatibility alias retained for presentation consumers."""

        return self.objective

    @classmethod
    def initial(cls) -> "AIThinkingSnapshot":
        return cls()


@dataclass(frozen=True, slots=True)
class AtlasReasoningSnapshot:
    current_action: str = "Unknown"
    why: str = "Unknown — no projected reasoning"
    risk_protection: str = "Unknown — no projected protection state"
    next_trigger: str = "Unknown — no projected trigger"
    tone: str = "neutral"

    @classmethod
    def initial(cls) -> "AtlasReasoningSnapshot":
        return cls()


__all__ = [
    "AtlasReasoningSnapshot",
    "AIThinkingSnapshot",
    "MissionStatusRow",
    "MissionStatusSnapshot",
]
