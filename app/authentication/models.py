from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value


class AuthenticationStatus(StrEnum):
    UNAUTHENTICATED = "UNAUTHENTICATED"
    AUTHENTICATING = "AUTHENTICATING"
    AUTHENTICATED = "AUTHENTICATED"
    LOGGED_OUT = "LOGGED_OUT"


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")
    return value


@dataclass(frozen=True, slots=True)
class AuthenticationRequest:
    broker_identifier: str
    credential_purpose: str
    required_value_names: tuple[str, ...]
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "broker_identifier", _text(self.broker_identifier, "broker_identifier"))
        object.__setattr__(self, "credential_purpose", _text(self.credential_purpose, "credential_purpose"))
        if not isinstance(self.required_value_names, tuple) or not self.required_value_names:
            raise ValueError("required_value_names must be a non-empty tuple")
        names = tuple(_text(value, "required_value_names") for value in self.required_value_names)
        if len(set(names)) != len(names):
            raise ValueError("required_value_names must be unique")
        object.__setattr__(self, "required_value_names", names)
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {
            "broker_identifier": self.broker_identifier,
            "credential_purpose": self.credential_purpose,
            "required_value_names": list(self.required_value_names),
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data["required_value_names"] = tuple(data.get("required_value_names", ()))
        return cls(**data)


@dataclass(frozen=True, slots=True)
class AuthenticationStateSnapshot:
    status: AuthenticationStatus
    transition_number: int
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.status, AuthenticationStatus):
            raise ValueError("status must be AuthenticationStatus")
        if isinstance(self.transition_number, bool) or not isinstance(self.transition_number, int) or self.transition_number < 0:
            raise ValueError("transition_number must be a non-negative integer")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"status": self.status.value, "transition_number": self.transition_number,
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data["status"] = AuthenticationStatus(data["status"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    success: bool
    state: AuthenticationStateSnapshot
    reason: str
    policy_version: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.success, bool):
            raise ValueError("success must be boolean")
        if not isinstance(self.state, AuthenticationStateSnapshot):
            raise ValueError("state must be AuthenticationStateSnapshot")
        object.__setattr__(self, "reason", _text(self.reason, "reason"))
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy_version"))
        expected = AuthenticationStatus.AUTHENTICATED if self.success else AuthenticationStatus.UNAUTHENTICATED
        if self.state.status is not expected:
            raise ValueError("result success does not match state")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"success": self.success, "state": self.state.to_dict(), "reason": self.reason,
                "policy_version": self.policy_version, "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data["state"] = AuthenticationStateSnapshot.from_dict(data["state"])
        return cls(**data)
