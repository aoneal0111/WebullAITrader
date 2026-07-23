from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value


@dataclass(frozen=True, slots=True)
class LearningPolicy:
    version: str = "learning_policy_v1"
    minimum_sample_size: int = 1
    include_trade_statistics: bool = True
    include_risk_statistics: bool = True
    include_strategy_statistics: bool = True
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a nonempty string")
        object.__setattr__(self, "version", self.version.strip())
        if isinstance(self.minimum_sample_size, bool) or not isinstance(self.minimum_sample_size, int):
            raise ValueError("minimum_sample_size must be an integer")
        if self.minimum_sample_size < 1:
            raise ValueError("minimum_sample_size must be at least one")
        for name in ("include_trade_statistics", "include_risk_statistics", "include_strategy_statistics"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "minimum_sample_size": self.minimum_sample_size,
                "include_trade_statistics": self.include_trade_statistics,
                "include_risk_statistics": self.include_risk_statistics,
                "include_strategy_statistics": self.include_strategy_statistics,
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LearningPolicy:
        if not isinstance(value, Mapping):
            raise ValueError("serialized policy must be a mapping")
        try:
            return cls(**dict(value))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize learning policy") from exc
