from dataclasses import dataclass, field
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value


@dataclass(frozen=True, slots=True)
class AuthenticationPolicy:
    version: str = "authentication_policy_v1"
    allow_reauthentication: bool = False
    strict_state_validation: bool = True
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be non-empty")
        if not isinstance(self.allow_reauthentication, bool):
            raise ValueError("allow_reauthentication must be boolean")
        if not isinstance(self.strict_state_validation, bool):
            raise ValueError("strict_state_validation must be boolean")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {
            "version": self.version,
            "allow_reauthentication": self.allow_reauthentication,
            "strict_state_validation": self.strict_state_validation,
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError("policy data must be a mapping")
        return cls(**dict(value))
