from dataclasses import dataclass, field
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value


@dataclass(frozen=True, slots=True)
class AuthenticationTransportPolicy:
    version: str = "authentication_transport_policy_v1"
    enabled: bool = False
    strict_validation: bool = True
    fail_authentication_on_transport_error: bool = True
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be non-empty")
        for name in ("enabled", "strict_validation", "fail_authentication_on_transport_error"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {
            "version": self.version, "enabled": self.enabled,
            "strict_validation": self.strict_validation,
            "fail_authentication_on_transport_error": self.fail_authentication_on_transport_error,
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError("policy data must be a mapping")
        return cls(**dict(value))
