from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value


@dataclass(frozen=True, slots=True)
class HTTPXTransportPolicy:
    version: str = "httpx_transport_policy_v1"
    enabled: bool = False
    timeout_seconds: Decimal = Decimal("10")
    follow_redirects: bool = False
    verify_response_type: bool = True
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be non-empty")
        for name in ("enabled", "follow_redirects", "verify_response_type"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        if not isinstance(self.timeout_seconds, Decimal) or isinstance(self.timeout_seconds, bool):
            raise ValueError("timeout_seconds must be Decimal")
        if not self.timeout_seconds.is_finite() or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {
            "version": self.version, "enabled": self.enabled,
            "timeout_seconds": str(self.timeout_seconds),
            "follow_redirects": self.follow_redirects,
            "verify_response_type": self.verify_response_type,
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise ValueError("policy data must be a mapping")
        data = dict(value)
        data["timeout_seconds"] = Decimal(data["timeout_seconds"])
        return cls(**data)
