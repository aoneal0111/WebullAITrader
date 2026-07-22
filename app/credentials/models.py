from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.credentials.exceptions import InvalidCredentialRequestError, InvalidCredentialResponseError


def _name(value, field_name, error_type):
    if not isinstance(value, str) or not value.strip():
        raise error_type(f"{field_name} must be non-empty")
    if value != value.strip():
        raise error_type(f"{field_name} must not contain surrounding whitespace")
    return value


def _names(values, field_name, error_type):
    if not isinstance(values, tuple):
        raise error_type(f"{field_name} must be an immutable tuple")
    normalized = tuple(_name(value, field_name, error_type) for value in values)
    if len(set(normalized)) != len(normalized):
        raise error_type(f"{field_name} must be unique")
    return normalized


@dataclass(frozen=True, slots=True)
class CredentialRequest:
    broker_identifier: str
    credential_purpose: str
    required_value_names: tuple[str, ...]
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "broker_identifier", _name(
            self.broker_identifier, "broker_identifier", InvalidCredentialRequestError))
        object.__setattr__(self, "credential_purpose", _name(
            self.credential_purpose, "credential_purpose", InvalidCredentialRequestError))
        object.__setattr__(self, "required_value_names", _names(
            self.required_value_names, "required_value_names", InvalidCredentialRequestError))
        if not self.required_value_names:
            raise InvalidCredentialRequestError("required_value_names cannot be empty")
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
        if not isinstance(value, Mapping):
            raise InvalidCredentialRequestError("request data must be a mapping")
        data = dict(value)
        data["required_value_names"] = tuple(data.get("required_value_names", ()))
        return cls(**data)


@dataclass(frozen=True, slots=True)
class CredentialResponse:
    broker_identifier: str
    credential_purpose: str
    values: Mapping[str, str]
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "broker_identifier", _name(
            self.broker_identifier, "broker_identifier", InvalidCredentialResponseError))
        object.__setattr__(self, "credential_purpose", _name(
            self.credential_purpose, "credential_purpose", InvalidCredentialResponseError))
        if not isinstance(self.values, Mapping):
            raise InvalidCredentialResponseError("values must be a mapping")
        values = {}
        for key, value in self.values.items():
            key = _name(key, "credential value name", InvalidCredentialResponseError)
            if not isinstance(value, str):
                raise InvalidCredentialResponseError("credential values must be strings")
            values[key] = value
        if not values:
            raise InvalidCredentialResponseError("values cannot be empty")
        object.__setattr__(self, "values", MappingProxyType(values))
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {
            "broker_identifier": self.broker_identifier,
            "credential_purpose": self.credential_purpose,
            "values": dict(self.values),
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping):
            raise InvalidCredentialResponseError("response data must be a mapping")
        return cls(**dict(value))
