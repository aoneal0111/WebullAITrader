from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.operations_core import OperationsEvent


class EventStoreStatus(StrEnum):
    EMPTY = "EMPTY"
    READY = "READY"
    ERROR = "ERROR"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class IndexedSession:
    session_id: str
    file_path: str
    started_at: datetime
    ended_at: datetime
    strategy_version: str
    application_version: str
    broker: str
    runtime_mode: str
    event_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "session_id",
            "file_path",
            "strategy_version",
            "application_version",
            "broker",
            "runtime_mode",
        ):
            _text(getattr(self, field_name), field_name)
        _timestamp(self.started_at, "started_at")
        _timestamp(self.ended_at, "ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        _positive(self.event_count, "event_count")


@dataclass(frozen=True, slots=True)
class IndexedEvent:
    session_id: str
    sequence_number: int
    timestamp: datetime
    event_type: str
    symbols: tuple[str, ...]
    order_ids: tuple[str, ...]
    position_ids: tuple[str, ...]
    decisions: tuple[str, ...]
    lifecycle_phases: tuple[str, ...]
    summary: str
    event: OperationsEvent

    def __post_init__(self) -> None:
        _text(self.session_id, "session_id")
        _positive(self.sequence_number, "sequence_number")
        _timestamp(self.timestamp, "timestamp")
        _text(self.event_type, "event_type")
        for field_name in (
            "symbols",
            "order_ids",
            "position_ids",
            "decisions",
            "lifecycle_phases",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, tuple):
                raise TypeError(f"{field_name} must be an immutable tuple")
            if any(
                not isinstance(item, str)
                or not item.strip()
                or item != item.strip()
                for item in value
            ):
                raise ValueError(
                    f"{field_name} must contain stripped non-empty text"
                )
            if len(value) != len(set(value)):
                raise ValueError(f"{field_name} must be unique")
        _text(self.summary, "summary")
        if not isinstance(self.event, OperationsEvent):
            raise TypeError("event must be an OperationsEvent")
        if self.timestamp != self.event.occurred_at:
            raise ValueError("timestamp must match event")
        if self.event_type != type(self.event).__name__:
            raise ValueError("event_type must match event")


@dataclass(frozen=True, slots=True)
class QueryStatistics:
    total_sessions: int
    total_events: int
    matched_events: int
    earliest_timestamp: datetime | None
    latest_timestamp: datetime | None
    event_type_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.total_sessions, "total_sessions"),
            (self.total_events, "total_events"),
            (self.matched_events, "matched_events"),
        ):
            _nonnegative(value, name)
        if self.matched_events > self.total_events:
            raise ValueError("matched_events cannot exceed total_events")
        for value, name in (
            (self.earliest_timestamp, "earliest_timestamp"),
            (self.latest_timestamp, "latest_timestamp"),
        ):
            if value is not None:
                _timestamp(value, name)
        if (
            self.earliest_timestamp is not None
            and self.latest_timestamp is not None
            and self.latest_timestamp < self.earliest_timestamp
        ):
            raise ValueError(
                "latest_timestamp cannot precede earliest_timestamp"
            )
        if not isinstance(self.event_type_counts, tuple):
            raise TypeError(
                "event_type_counts must be an immutable tuple"
            )
        for event_type, count in self.event_type_counts:
            _text(event_type, "event_type")
            _positive(count, "event_type count")

    @classmethod
    def empty(cls) -> "QueryStatistics":
        return cls(0, 0, 0, None, None, ())


@dataclass(frozen=True, slots=True)
class QueryResult:
    query: str
    events: tuple[IndexedEvent, ...]
    statistics: QueryStatistics

    def __post_init__(self) -> None:
        _text(self.query, "query")
        if not isinstance(self.events, tuple):
            raise TypeError("events must be an immutable tuple")
        if any(
            not isinstance(event, IndexedEvent)
            for event in self.events
        ):
            raise TypeError(
                "events must contain only IndexedEvent instances"
            )
        if not isinstance(self.statistics, QueryStatistics):
            raise TypeError("statistics must be QueryStatistics")
        if self.statistics.matched_events != len(self.events):
            raise ValueError(
                "statistics matched_events must match result events"
            )

    @classmethod
    def empty(cls) -> "QueryResult":
        return cls("all", (), QueryStatistics.empty())


@dataclass(frozen=True, slots=True)
class EventStoreSnapshot:
    status: EventStoreStatus
    sessions: tuple[IndexedSession, ...]
    result: QueryResult
    statistics: QueryStatistics
    available_symbols: tuple[str, ...]
    available_event_types: tuple[str, ...]
    last_refresh: datetime | None
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, EventStoreStatus):
            raise TypeError("status must be EventStoreStatus")
        if not isinstance(self.sessions, tuple):
            raise TypeError("sessions must be an immutable tuple")
        if any(
            not isinstance(session, IndexedSession)
            for session in self.sessions
        ):
            raise TypeError(
                "sessions must contain only IndexedSession instances"
            )
        if not isinstance(self.result, QueryResult):
            raise TypeError("result must be QueryResult")
        if not isinstance(self.statistics, QueryStatistics):
            raise TypeError("statistics must be QueryStatistics")
        for value, name in (
            (self.available_symbols, "available_symbols"),
            (self.available_event_types, "available_event_types"),
            (self.errors, "errors"),
        ):
            if not isinstance(value, tuple):
                raise TypeError(f"{name} must be an immutable tuple")
            if any(
                not isinstance(item, str) or not item.strip()
                for item in value
            ):
                raise ValueError(f"{name} contains invalid text")
        if self.last_refresh is not None:
            _timestamp(self.last_refresh, "last_refresh")

    @classmethod
    def initial(cls) -> "EventStoreSnapshot":
        empty = QueryStatistics.empty()
        return cls(
            EventStoreStatus.EMPTY,
            (),
            QueryResult.empty(),
            empty,
            (),
            (),
            None,
            (),
        )


def _text(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
    ):
        raise ValueError(f"{name} must be stripped non-empty text")


def _timestamp(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


def _positive(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _nonnegative(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
