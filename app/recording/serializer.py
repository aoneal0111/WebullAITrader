from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import inspect
import json
from typing import Any
from uuid import UUID

from app.operations_core import OperationsEvent
from app.operations_core import events as operations_events

from .models import (
    RECORDING_SCHEMA_VERSION,
    RecordedEvent,
    RecordedSession,
)


class RecordingFormatError(ValueError):
    """Raised when a recording is corrupted or unsupported."""


class RecordingSerializer:
    def serialize(self, session: RecordedSession) -> bytes:
        if not isinstance(session, RecordedSession):
            raise TypeError("session must be a RecordedSession")
        session_document = _session_to_document(session)
        checksum = _checksum(session_document)
        envelope = {
            "format": "WebullAITrader.Session",
            "schema_version": session.schema_version,
            "checksum": checksum,
            "session": session_document,
        }
        return _canonical_json(envelope)

    def deserialize(self, data: bytes) -> RecordedSession:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RecordingFormatError(
                "recording is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(document, dict):
            raise RecordingFormatError(
                "recording envelope must be an object"
            )
        if document.get("format") != "WebullAITrader.Session":
            raise RecordingFormatError("unsupported recording format")
        version = document.get("schema_version")
        _validate_supported_version(version)
        session_document = document.get("session")
        if not isinstance(session_document, dict):
            raise RecordingFormatError("recording session is missing")
        checksum = document.get("checksum")
        if (
            not isinstance(checksum, str)
            or checksum != _checksum(session_document)
        ):
            raise RecordingFormatError("recording checksum mismatch")
        try:
            return _session_from_document(
                session_document,
                schema_version=version,
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise RecordingFormatError(
                f"recording session is invalid: {exc}"
            ) from exc

    def record_event(
        self,
        event: OperationsEvent,
        sequence_number: int,
    ) -> RecordedEvent:
        if not isinstance(event, OperationsEvent):
            raise TypeError("event must be an OperationsEvent")
        payload = tuple(
            (field.name, getattr(event, field.name))
            for field in fields(event)
            if field.name not in {"occurred_at", "event_id", "source"}
        )
        metadata = (
            ("event_id", event.event_id),
            ("source", event.source),
        )
        return RecordedEvent(
            sequence_number=sequence_number,
            timestamp=event.occurred_at,
            event_type=type(event).__name__,
            payload=payload,
            metadata=metadata,
        )

    def restore_event(self, recorded: RecordedEvent) -> OperationsEvent:
        if not isinstance(recorded, RecordedEvent):
            raise TypeError("recorded must be a RecordedEvent")
        event_type = _event_types().get(recorded.event_type)
        if event_type is None:
            raise RecordingFormatError(
                f"unsupported event type: {recorded.event_type}"
            )
        values = dict(recorded.payload)
        metadata = dict(recorded.metadata)
        event_id = metadata.get("event_id")
        source = metadata.get("source")
        if not isinstance(event_id, UUID):
            raise RecordingFormatError(
                "event metadata must contain a UUID event_id"
            )
        if not isinstance(source, str) or not source.strip():
            raise RecordingFormatError(
                "event metadata must contain a source"
            )
        values.update(
            occurred_at=recorded.timestamp,
            event_id=event_id,
            source=source,
        )
        try:
            event = event_type(**values)
        except (TypeError, ValueError) as exc:
            raise RecordingFormatError(
                f"invalid {recorded.event_type} payload"
            ) from exc
        if not isinstance(event, OperationsEvent):
            raise RecordingFormatError(
                "restored object is not an OperationsEvent"
            )
        return event


def _session_to_document(session: RecordedSession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "started_at": _encode(session.started_at),
        "ended_at": _encode(session.ended_at),
        "strategy_version": session.strategy_version,
        "application_version": session.application_version,
        "broker": session.broker,
        "runtime_mode": session.runtime_mode,
        "events": [
            {
                "sequence_number": event.sequence_number,
                "timestamp": _encode(event.timestamp),
                "event_type": event.event_type,
                "payload": _encode_pairs(event.payload),
                "metadata": _encode_pairs(event.metadata),
                "schema_version": event.schema_version,
            }
            for event in session.events
        ],
    }


def _session_from_document(
    document: dict[str, Any],
    *,
    schema_version: int,
) -> RecordedSession:
    events_document = document["events"]
    if not isinstance(events_document, list):
        raise RecordingFormatError("events must be an array")
    events = tuple(
        RecordedEvent(
            sequence_number=item["sequence_number"],
            timestamp=_decode(item["timestamp"]),
            event_type=item["event_type"],
            payload=_decode_pairs(item["payload"]),
            metadata=_decode_pairs(item["metadata"]),
            schema_version=item.get(
                "schema_version",
                schema_version,
            ),
        )
        for item in events_document
        if isinstance(item, dict)
    )
    if len(events) != len(events_document):
        raise RecordingFormatError("events must contain objects")
    return RecordedSession(
        session_id=document["session_id"],
        started_at=_decode(document["started_at"]),
        ended_at=_decode(document["ended_at"]),
        strategy_version=document["strategy_version"],
        application_version=document["application_version"],
        broker=document["broker"],
        runtime_mode=document["runtime_mode"],
        events=events,
        schema_version=schema_version,
    )


def _encode_pairs(
    pairs: tuple[tuple[str, object], ...],
) -> list[dict[str, Any]]:
    return [
        {"name": name, "value": _encode(value)}
        for name, value in pairs
    ]


def _decode_pairs(
    values: object,
) -> tuple[tuple[str, object], ...]:
    if not isinstance(values, list):
        raise RecordingFormatError("key-value pairs must be an array")
    pairs: list[tuple[str, object]] = []
    for item in values:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or "value" not in item
        ):
            raise RecordingFormatError(
                "invalid key-value pair"
            )
        pairs.append((item["name"], _decode(item["value"])))
    return tuple(pairs)


def _encode(value: object) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return {"$type": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"$type": "datetime", "value": value.isoformat()}
    if isinstance(value, UUID):
        return {"$type": "uuid", "value": str(value)}
    if isinstance(value, Enum):
        return {
            "$type": "enum",
            "class": _class_name(type(value)),
            "value": _encode(value.value),
        }
    if isinstance(value, tuple):
        return {
            "$type": "tuple",
            "items": [_encode(item) for item in value],
        }
    if is_dataclass(value):
        return {
            "$type": "dataclass",
            "class": _class_name(type(value)),
            "fields": {
                field.name: _encode(getattr(value, field.name))
                for field in fields(value)
            },
        }
    raise RecordingFormatError(
        f"unsupported payload value: {type(value).__name__}"
    )


def _decode(value: Any) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if not isinstance(value, dict):
        raise RecordingFormatError("invalid encoded value")
    kind = value.get("$type")
    if kind == "decimal":
        return Decimal(value["value"])
    if kind == "datetime":
        result = datetime.fromisoformat(value["value"])
        if result.tzinfo is None:
            raise RecordingFormatError(
                "decoded datetime must be timezone-aware"
            )
        return result
    if kind == "uuid":
        return UUID(value["value"])
    if kind == "tuple":
        items = value.get("items")
        if not isinstance(items, list):
            raise RecordingFormatError("tuple items must be an array")
        return tuple(_decode(item) for item in items)
    if kind in {"dataclass", "enum"}:
        class_name = value.get("class")
        target = _safe_types().get(class_name)
        if target is None:
            raise RecordingFormatError(
                f"unsupported payload class: {class_name}"
            )
        if kind == "enum":
            return target(_decode(value["value"]))
        field_values = value.get("fields")
        if not isinstance(field_values, dict):
            raise RecordingFormatError(
                "dataclass fields must be an object"
            )
        return target(
            **{
                name: _decode(field_value)
                for name, field_value in field_values.items()
            }
        )
    raise RecordingFormatError(f"unsupported encoded type: {kind}")


def _event_types() -> dict[str, type[OperationsEvent]]:
    return {
        name: value
        for name, value in inspect.getmembers(
            operations_events,
            inspect.isclass,
        )
        if issubclass(value, OperationsEvent)
        and value is not OperationsEvent
    }


def _safe_types() -> dict[str, type]:
    return {
        _class_name(value): value
        for _, value in inspect.getmembers(
            operations_events,
            inspect.isclass,
        )
        if is_dataclass(value) or issubclass(value, Enum)
    }


def _class_name(value: type) -> str:
    return f"{value.__module__}.{value.__qualname__}"


def _validate_supported_version(value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value != RECORDING_SCHEMA_VERSION
    ):
        raise RecordingFormatError(
            f"unsupported schema version: {value}"
        )


def _checksum(document: dict[str, Any]) -> str:
    return sha256(_canonical_json(document)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
