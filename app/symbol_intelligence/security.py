"""Secret-safe bounded serialization helpers for symbol intelligence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit


FORBIDDEN_KEY_PARTS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "account_id",
    "user_agent",
)
MAX_METADATA_KEYS = 32
MAX_METADATA_DEPTH = 4
MAX_METADATA_SEQUENCE = 64
MAX_METADATA_STRING = 2_048


class UnsafePayloadError(ValueError):
    """Raised before sensitive or unbounded data can reach persistence."""


def validate_safe_key(key: str) -> str:
    normalized = str(key).strip()
    folded = normalized.casefold()
    if not normalized or any(part in folded for part in FORBIDDEN_KEY_PARTS):
        raise UnsafePayloadError("payload contains a forbidden metadata key")
    if len(normalized) > 64:
        raise UnsafePayloadError("metadata key exceeds the bounded length")
    return normalized


def freeze_metadata(
    value: Mapping[str, Any] | None,
    *,
    allowed_keys: frozenset[str],
) -> Mapping[str, Any]:
    """Validate, bound and recursively freeze an allowlisted JSON object."""

    source = {} if value is None else value
    if not isinstance(source, Mapping):
        raise UnsafePayloadError("metadata must be an object")
    if len(source) > MAX_METADATA_KEYS:
        raise UnsafePayloadError("metadata exceeds the key bound")
    frozen: dict[str, Any] = {}
    for raw_key, raw_value in source.items():
        key = validate_safe_key(str(raw_key))
        if key not in allowed_keys:
            raise UnsafePayloadError(f"metadata key is not allowlisted: {key}")
        frozen[key] = _freeze_value(raw_value, depth=1)
    return MappingProxyType(dict(sorted(frozen.items())))


def safe_json_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON-compatible value after recursive key validation."""

    return _freeze_value(value, depth=depth)


def sanitize_source_reference(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > 2_048:
        raise ValueError("source reference exceeds the bounded length")
    parsed = urlsplit(text)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise ValueError("source reference must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafePayloadError("source reference must not contain credentials")
    host = parsed.hostname.encode("idna").decode("ascii").casefold()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit(("https", host, parsed.path or "/", "", ""))


def validate_safe_text(value: str, *, field: str, maximum: int) -> str:
    text = " ".join(str(value).strip().split())
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > maximum:
        raise ValueError(f"{field} exceeds the bounded length")
    folded = text.casefold()
    if any(part in folded for part in FORBIDDEN_KEY_PARTS):
        raise UnsafePayloadError(f"{field} contains a forbidden secret label")
    return text


def _freeze_value(value: Any, *, depth: int) -> Any:
    if depth > MAX_METADATA_DEPTH:
        raise UnsafePayloadError("metadata exceeds the nesting bound")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > MAX_METADATA_STRING:
            raise UnsafePayloadError("metadata string exceeds the bounded length")
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_METADATA_KEYS:
            raise UnsafePayloadError("nested metadata exceeds the key bound")
        return MappingProxyType({
            validate_safe_key(str(key)): _freeze_value(item, depth=depth + 1)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        })
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > MAX_METADATA_SEQUENCE:
            raise UnsafePayloadError("metadata sequence exceeds the item bound")
        return tuple(_freeze_value(item, depth=depth + 1) for item in value)
    raise UnsafePayloadError("metadata contains a non-JSON value")


__all__ = [
    "FORBIDDEN_KEY_PARTS",
    "UnsafePayloadError",
    "freeze_metadata",
    "safe_json_value",
    "sanitize_source_reference",
    "validate_safe_key",
    "validate_safe_text",
]
