from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value


class SessionStatus(StrEnum):
    NO_SESSION = "NO_SESSION"
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")
    return value


@dataclass(frozen=True, slots=True)
class SessionIdentifier:
    value: str

    def __post_init__(self):
        object.__setattr__(self, "value", _text(self.value, "session identifier"))

    def to_dict(self):
        return {"value": self.value}

    @classmethod
    def from_dict(cls, value):
        return cls(value["value"])


@dataclass(frozen=True, slots=True)
class SessionRequest:
    identifier: SessionIdentifier
    purpose: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.identifier, SessionIdentifier):
            raise ValueError("identifier must be SessionIdentifier")
        object.__setattr__(self, "purpose", _text(self.purpose, "purpose"))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"identifier": self.identifier.to_dict(), "purpose": self.purpose,
                "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data["identifier"] = SessionIdentifier.from_dict(data["identifier"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class Session:
    identifier: SessionIdentifier
    purpose: str
    status: SessionStatus
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.identifier, SessionIdentifier):
            raise ValueError("identifier must be SessionIdentifier")
        object.__setattr__(self, "purpose", _text(self.purpose, "purpose"))
        if not isinstance(self.status, SessionStatus) or self.status is SessionStatus.NO_SESSION:
            raise ValueError("session status must represent an existing session")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {"identifier": self.identifier.to_dict(), "purpose": self.purpose,
                "status": self.status.value, "metadata": thaw_json_value(self.metadata)}

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data["identifier"] = SessionIdentifier.from_dict(data["identifier"])
        data["status"] = SessionStatus(data["status"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    status: SessionStatus
    session: Session | None
    replaced_identifiers: tuple[SessionIdentifier, ...]
    transition_number: int
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.status, SessionStatus):
            raise ValueError("status must be SessionStatus")
        if self.status is SessionStatus.NO_SESSION and self.session is not None:
            raise ValueError("NO_SESSION cannot contain a session")
        if self.status is not SessionStatus.NO_SESSION:
            if not isinstance(self.session, Session) or self.session.status is not self.status:
                raise ValueError("snapshot state must match session state")
        if not isinstance(self.replaced_identifiers, tuple) or any(
                not isinstance(value, SessionIdentifier) for value in self.replaced_identifiers):
            raise ValueError("replaced_identifiers must be an immutable identifier tuple")
        if len(set(self.replaced_identifiers)) != len(self.replaced_identifiers):
            raise ValueError("replaced_identifiers must be unique")
        if isinstance(self.transition_number, bool) or not isinstance(self.transition_number, int) or self.transition_number < 0:
            raise ValueError("transition_number must be a non-negative integer")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {
            "status": self.status.value, "session": self.session.to_dict() if self.session else None,
            "replaced_identifiers": [value.to_dict() for value in self.replaced_identifiers],
            "transition_number": self.transition_number, "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data["status"] = SessionStatus(data["status"])
        data["session"] = Session.from_dict(data["session"]) if data["session"] else None
        data["replaced_identifiers"] = tuple(
            SessionIdentifier.from_dict(item) for item in data["replaced_identifiers"])
        return cls(**data)
