from dataclasses import dataclass, field
from typing import Mapping

from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.http_runtime import HTTPMethod


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    if value != value.strip():
        raise ValueError(f"{name} must not contain surrounding whitespace")
    return value


def _pairs(value, name):
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be an immutable tuple")
    result = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(f"{name} entries must be key/value tuples")
        key, item_value = item
        if not isinstance(item_value, str) or not item_value.strip():
            raise ValueError(f"{name} value must be non-empty")
        result.append((_text(key, f"{name} key"), item_value))
    return tuple(result)


def _body(value):
    return freeze_json_mapping("body", {"value": value})["value"]


@dataclass(frozen=True, slots=True)
class PipelineContext:
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
class HTTPRequestOperation:
    request_id: str
    method: HTTPMethod
    url: str
    headers: tuple[tuple[str, str], ...]
    query_parameters: tuple[tuple[str, str], ...]
    body: JSONValue
    context: PipelineContext
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "request_id", _text(self.request_id, "request_id"))
        if not isinstance(self.method, HTTPMethod):
            raise ValueError("method must be HTTPMethod")
        object.__setattr__(self, "url", _text(self.url, "url"))
        object.__setattr__(self, "headers", _pairs(self.headers, "headers"))
        object.__setattr__(self, "query_parameters", _pairs(self.query_parameters, "query_parameters"))
        object.__setattr__(self, "body", _body(self.body))
        if not isinstance(self.context, PipelineContext):
            raise ValueError("context must be PipelineContext")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {
            "request_id": self.request_id, "method": self.method.value, "url": self.url,
            "headers": [list(item) for item in self.headers],
            "query_parameters": [list(item) for item in self.query_parameters],
            "body": thaw_json_value(self.body), "context": self.context.to_dict(),
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data["method"] = HTTPMethod(data["method"])
        data["headers"] = tuple(tuple(item) for item in data["headers"])
        data["query_parameters"] = tuple(tuple(item) for item in data["query_parameters"])
        data["context"] = PipelineContext.from_dict(data["context"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class HTTPResponseOperation:
    response_id: str
    status_code: int
    headers: tuple[tuple[str, str], ...]
    body: JSONValue
    context: PipelineContext
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "response_id", _text(self.response_id, "response_id"))
        if isinstance(self.status_code, bool) or not isinstance(self.status_code, int) or not 100 <= self.status_code <= 599:
            raise ValueError("status_code must be an integer from 100 through 599")
        object.__setattr__(self, "headers", _pairs(self.headers, "headers"))
        object.__setattr__(self, "body", _body(self.body))
        if not isinstance(self.context, PipelineContext):
            raise ValueError("context must be PipelineContext")
        object.__setattr__(self, "metadata", freeze_json_mapping("metadata", self.metadata))

    def to_dict(self):
        return {
            "response_id": self.response_id, "status_code": self.status_code,
            "headers": [list(item) for item in self.headers], "body": thaw_json_value(self.body),
            "context": self.context.to_dict(), "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, value):
        data = dict(value)
        data["headers"] = tuple(tuple(item) for item in data["headers"])
        data["context"] = PipelineContext.from_dict(data["context"])
        return cls(**data)
