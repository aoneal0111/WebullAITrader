from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value


@dataclass(frozen=True, slots=True)
class OutcomePolicy:
    version: str = "outcome_policy_v1"
    include_execution_metadata: bool = True
    include_checks: bool = True
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a nonempty string")
        object.__setattr__(self, "version", self.version.strip())
        for name in ("include_execution_metadata", "include_checks"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version,
                "include_execution_metadata": self.include_execution_metadata,
                "include_checks": self.include_checks,
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> OutcomePolicy:
        if not isinstance(value, Mapping):
            raise ValueError("serialized policy must be a mapping")
        try:
            return cls(**dict(value))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Unable to deserialize outcome policy") from exc
