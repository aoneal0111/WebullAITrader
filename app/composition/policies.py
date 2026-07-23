from dataclasses import dataclass, field
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value


@dataclass(frozen=True, slots=True)
class CompositionPolicy:
    version: str = "composition_policy_v1"
    strict_validation: bool = True
    allow_overrides: bool = False
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be non-empty")
        if not isinstance(self.strict_validation, bool):
            raise ValueError("strict_validation must be boolean")
        if not isinstance(self.allow_overrides, bool):
            raise ValueError("allow_overrides must be boolean")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {
            "version": self.version,
            "strict_validation": self.strict_validation,
            "allow_overrides": self.allow_overrides,
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError("policy data must be a mapping")
        return cls(**dict(value))
