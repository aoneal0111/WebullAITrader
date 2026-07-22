from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


DEFAULT_AGENT_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {"technical_agent_v1": 1.0}
)


@dataclass(frozen=True, slots=True)
class AgentWeightConfiguration:
    version: str = "committee_weights_v1"
    weights: Mapping[str, float] = field(
        default_factory=lambda: DEFAULT_AGENT_WEIGHTS
    )
    default_weight: float = 1.0
    minimum_confidence: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a nonempty string")
        if not isinstance(self.weights, Mapping):
            raise ValueError("weights must be a mapping")
        normalized: dict[str, float] = {}
        for name, weight in self.weights.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("weight agent names must be nonempty strings")
            normalized[name.strip()] = _unit_weight("weight", weight)
        object.__setattr__(self, "version", self.version.strip())
        object.__setattr__(self, "weights", MappingProxyType(normalized))
        object.__setattr__(
            self,
            "default_weight",
            _unit_weight("default_weight", self.default_weight),
        )
        object.__setattr__(
            self,
            "minimum_confidence",
            _unit_weight("minimum_confidence", self.minimum_confidence),
        )

    def weight_for(self, agent_name: str) -> float:
        if not isinstance(agent_name, str) or not agent_name.strip():
            raise ValueError("agent_name must be a nonempty string")
        return self.weights.get(agent_name.strip(), self.default_weight)


def _unit_weight(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return normalized
