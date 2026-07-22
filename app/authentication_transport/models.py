from dataclasses import dataclass, field
from typing import Mapping

from app.authentication import AuthenticationRequest, AuthenticationResult
from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")
    return value


@dataclass(frozen=True, slots=True)
class AuthenticationTransportContext:
    correlation_id: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "correlation_id", _text(self.correlation_id, "correlation_id"))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"correlation_id": self.correlation_id, "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class AuthenticationVerificationResult:
    success: bool
    reason: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.success, bool):
            raise ValueError("success must be boolean")
        object.__setattr__(self, "reason", _text(self.reason, "reason"))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"success": self.success, "reason": self.reason,
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        return cls(**dict(value))


@dataclass(frozen=True, slots=True)
class AuthenticationTransportRequest:
    attempt_id: str
    authentication_request: AuthenticationRequest
    context: AuthenticationTransportContext
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "attempt_id", _text(self.attempt_id, "attempt_id"))
        if not isinstance(self.authentication_request, AuthenticationRequest):
            raise ValueError("authentication_request must be AuthenticationRequest")
        if not isinstance(self.context, AuthenticationTransportContext):
            raise ValueError("context must be AuthenticationTransportContext")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {
            "attempt_id": self.attempt_id,
            "authentication_request": self.authentication_request.to_dict(),
            "context": self.context.to_dict(), "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data["authentication_request"] = AuthenticationRequest.from_dict(data["authentication_request"])
        data["context"] = AuthenticationTransportContext.from_dict(data["context"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class AuthenticationTransportResult:
    attempt_id: str
    success: bool
    verification: AuthenticationVerificationResult
    authentication_result: AuthenticationResult | None
    response_identifier: str
    context: AuthenticationTransportContext
    policy_version: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "attempt_id", _text(self.attempt_id, "attempt_id"))
        if not isinstance(self.success, bool):
            raise ValueError("success must be boolean")
        if not isinstance(self.verification, AuthenticationVerificationResult):
            raise ValueError("verification must be AuthenticationVerificationResult")
        if self.success != self.verification.success:
            raise ValueError("result success must match verification")
        if self.success and not isinstance(self.authentication_result, AuthenticationResult):
            raise ValueError("successful result requires AuthenticationResult")
        if not self.success and self.authentication_result is not None:
            raise ValueError("failed result cannot include AuthenticationResult")
        object.__setattr__(self, "response_identifier", _text(
            self.response_identifier, "response_identifier"))
        if not isinstance(self.context, AuthenticationTransportContext):
            raise ValueError("context must be AuthenticationTransportContext")
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy_version"))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {
            "attempt_id": self.attempt_id, "success": self.success,
            "verification": self.verification.to_dict(),
            "authentication_result": self.authentication_result.to_dict() if self.authentication_result else None,
            "response_identifier": self.response_identifier, "context": self.context.to_dict(),
            "policy_version": self.policy_version, "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data["verification"] = AuthenticationVerificationResult.from_dict(data["verification"])
        data["authentication_result"] = (
            AuthenticationResult.from_dict(data["authentication_result"])
            if data["authentication_result"] else None)
        data["context"] = AuthenticationTransportContext.from_dict(data["context"])
        return cls(**data)
