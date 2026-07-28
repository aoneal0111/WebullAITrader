from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum, StrEnum
from pathlib import Path
from uuid import UUID


RECORDING_SCHEMA_VERSION = 1


class RecordingState(StrEnum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    STOPPED = "STOPPED"


class RecordingStatus(StrEnum):
    READY = "READY"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


ImmutableValue = (
    None
    | bool
    | int
    | float
    | str
    | Decimal
    | datetime
    | UUID
    | Enum
    | tuple
    | object
)


@dataclass(frozen=True, slots=True)
class RecordedEvent:
    sequence_number: int
    timestamp: datetime
    event_type: str
    payload: tuple[tuple[str, ImmutableValue], ...]
    metadata: tuple[tuple[str, ImmutableValue], ...]
    schema_version: int = RECORDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_positive_integer(
            self.sequence_number,
            "sequence_number",
        )
        _validate_timestamp(self.timestamp, "timestamp")
        _validate_text(self.event_type, "event_type")
        _validate_pairs(self.payload, "payload")
        _validate_pairs(self.metadata, "metadata")
        _validate_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class RecordedSession:
    session_id: str
    started_at: datetime
    ended_at: datetime
    strategy_version: str
    application_version: str
    broker: str
    runtime_mode: str
    events: tuple[RecordedEvent, ...]
    schema_version: int = RECORDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "session_id",
            "strategy_version",
            "application_version",
            "broker",
            "runtime_mode",
        ):
            _validate_text(getattr(self, field_name), field_name)
        _validate_timestamp(self.started_at, "started_at")
        _validate_timestamp(self.ended_at, "ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        if not isinstance(self.events, tuple):
            raise TypeError("events must be an immutable tuple")
        if not self.events:
            raise ValueError("recorded session must contain events")
        if any(
            not isinstance(event, RecordedEvent)
            for event in self.events
        ):
            raise TypeError(
                "events must contain only RecordedEvent instances"
            )
        expected = tuple(range(1, len(self.events) + 1))
        actual = tuple(
            event.sequence_number for event in self.events
        )
        if actual != expected:
            raise ValueError(
                "event sequence numbers must be contiguous"
            )
        if any(
            event.schema_version != self.schema_version
            for event in self.events
        ):
            raise ValueError(
                "event schema versions must match session schema"
            )
        _validate_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class RecordingSnapshot:
    state: RecordingState
    status: RecordingStatus
    session_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: Decimal
    event_count: int
    size_bytes: int
    file_path: str | None
    error: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.state, RecordingState):
            raise TypeError("state must be a RecordingState")
        if not isinstance(self.status, RecordingStatus):
            raise TypeError("status must be a RecordingStatus")
        if self.session_id is not None:
            _validate_text(self.session_id, "session_id")
        for value, field_name in (
            (self.started_at, "started_at"),
            (self.ended_at, "ended_at"),
        ):
            if value is not None:
                _validate_timestamp(value, field_name)
        if (
            self.started_at is not None
            and self.ended_at is not None
            and self.ended_at < self.started_at
        ):
            raise ValueError("ended_at cannot precede started_at")
        if (
            not isinstance(self.duration_seconds, Decimal)
            or not self.duration_seconds.is_finite()
            or self.duration_seconds < 0
        ):
            raise ValueError(
                "duration_seconds must be a finite nonnegative Decimal"
            )
        for value, field_name in (
            (self.event_count, "event_count"),
            (self.size_bytes, "size_bytes"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(
                    f"{field_name} must be a nonnegative integer"
                )
        if self.file_path is not None:
            _validate_text(self.file_path, "file_path")
        if self.error is not None:
            _validate_text(self.error, "error")
        if self.state is RecordingState.IDLE and any(
            value is not None
            for value in (
                self.session_id,
                self.started_at,
                self.ended_at,
            )
        ):
            raise ValueError("IDLE recording cannot contain a session")
        if self.state is RecordingState.IDLE and (
            self.status is not RecordingStatus.READY
        ):
            raise ValueError("IDLE recording must have READY status")
        if self.state is RecordingState.RECORDING:
            if (
                self.status is not RecordingStatus.ACTIVE
                or self.session_id is None
                or self.started_at is None
                or self.ended_at is not None
            ):
                raise ValueError(
                    "RECORDING state requires an active session"
                )
        if self.state is RecordingState.STOPPED and (
            self.session_id is None
            or self.started_at is None
            or self.ended_at is None
            or self.status
            not in {
                RecordingStatus.COMPLETED,
                RecordingStatus.ERROR,
            }
        ):
            raise ValueError(
                "STOPPED state requires a completed session"
            )

    @classmethod
    def initial(cls) -> "RecordingSnapshot":
        return cls(
            state=RecordingState.IDLE,
            status=RecordingStatus.READY,
            session_id=None,
            started_at=None,
            ended_at=None,
            duration_seconds=Decimal("0"),
            event_count=0,
            size_bytes=0,
            file_path=None,
            error=None,
        )


def _validate_pairs(
    value: tuple[tuple[str, ImmutableValue], ...],
    field_name: str,
) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be an immutable tuple")
    keys: list[str] = []
    for item in value:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
        ):
            raise TypeError(
                f"{field_name} must contain immutable key-value pairs"
            )
        _validate_text(item[0], f"{field_name} key")
        _validate_immutable_value(item[1], field_name)
        keys.append(item[0])
    if len(keys) != len(set(keys)):
        raise ValueError(f"{field_name} keys must be unique")


def _validate_immutable_value(value: object, field_name: str) -> None:
    if value is None or isinstance(
        value,
        (bool, int, float, str, Decimal, datetime, UUID, Enum),
    ):
        return
    if isinstance(value, tuple):
        for item in value:
            _validate_immutable_value(item, field_name)
        return
    if is_dataclass(value):
        params = getattr(type(value), "__dataclass_params__", None)
        if params is None or not params.frozen:
            raise TypeError(
                f"{field_name} dataclass values must be frozen"
            )
        for data_field in fields(value):
            _validate_immutable_value(
                getattr(value, data_field.name),
                field_name,
            )
        return
    raise TypeError(f"{field_name} contains a mutable value")


def _validate_schema_version(value: int) -> None:
    _validate_positive_integer(value, "schema_version")


def _validate_positive_integer(value: int, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValueError(f"{field_name} must be a positive integer")


def _validate_timestamp(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _validate_text(value: str, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
    ):
        raise ValueError(f"{field_name} must be stripped non-empty text")
