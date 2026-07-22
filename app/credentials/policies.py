from dataclasses import dataclass, field
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value


@dataclass(frozen=True, slots=True)
class CredentialPolicy:
    version: str = "credential_policy_v1"
    provider_enabled: bool = False
    require_non_empty_values: bool = True
    allow_additional_values: bool = False
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be non-empty")
        for name in ("provider_enabled", "require_non_empty_values", "allow_additional_values"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {
            "version": self.version,
            "provider_enabled": self.provider_enabled,
            "require_non_empty_values": self.require_non_empty_values,
            "allow_additional_values": self.allow_additional_values,
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError("policy data must be a mapping")
        return cls(**dict(value))
