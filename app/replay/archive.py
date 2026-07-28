from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from app.operations_core import OperationsEvent

from .models import ReplaySession


@dataclass(frozen=True, slots=True)
class ReplayArchiveEntry:
    timestamp: datetime
    sequence_number: int
    event_type: str
    event_payload: OperationsEvent

    def __post_init__(self) -> None:
        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is None
        ):
            raise ValueError("timestamp must be timezone-aware")
        if (
            isinstance(self.sequence_number, bool)
            or not isinstance(self.sequence_number, int)
            or self.sequence_number <= 0
        ):
            raise ValueError(
                "sequence_number must be a positive integer"
            )
        if (
            not isinstance(self.event_type, str)
            or not self.event_type.strip()
            or self.event_type != self.event_type.strip()
        ):
            raise ValueError(
                "event_type must be stripped non-empty text"
            )
        if not isinstance(self.event_payload, OperationsEvent):
            raise TypeError(
                "event_payload must be an OperationsEvent"
            )
        if self.event_type != type(self.event_payload).__name__:
            raise ValueError("event_type must match event_payload")
        if self.timestamp != self.event_payload.occurred_at:
            raise ValueError("timestamp must match event_payload")


@dataclass(frozen=True, slots=True)
class ReplayEventArchive:
    entries: tuple[ReplayArchiveEntry, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple):
            raise TypeError("entries must be an immutable tuple")
        if any(
            not isinstance(entry, ReplayArchiveEntry)
            for entry in self.entries
        ):
            raise TypeError(
                "entries must contain only ReplayArchiveEntry instances"
            )
        expected = tuple(range(1, len(self.entries) + 1))
        actual = tuple(
            entry.sequence_number
            for entry in self.entries
        )
        if actual != expected:
            raise ValueError(
                "sequence numbers must be contiguous publication order"
            )

    @classmethod
    def from_events(
        cls,
        events: Iterable[OperationsEvent],
    ) -> "ReplayEventArchive":
        immutable_events = tuple(events)
        if any(
            not isinstance(event, OperationsEvent)
            for event in immutable_events
        ):
            raise TypeError(
                "events must contain only OperationsEvent instances"
            )
        return cls(
            entries=tuple(
                ReplayArchiveEntry(
                    timestamp=event.occurred_at,
                    sequence_number=index,
                    event_type=type(event).__name__,
                    event_payload=event,
                )
                for index, event in enumerate(
                    immutable_events,
                    start=1,
                )
            )
        )

    def append(
        self,
        event: OperationsEvent,
    ) -> "ReplayEventArchive":
        if not isinstance(event, OperationsEvent):
            raise TypeError("event must be an OperationsEvent")
        return ReplayEventArchive(
            entries=self.entries
            + (
                ReplayArchiveEntry(
                    timestamp=event.occurred_at,
                    sequence_number=len(self.entries) + 1,
                    event_type=type(event).__name__,
                    event_payload=event,
                ),
            )
        )

    def session(self, session_id: str) -> ReplaySession:
        if not self.entries:
            raise ValueError("cannot create a session from an empty archive")
        return ReplaySession(
            session_id=session_id,
            started_at=min(entry.timestamp for entry in self.entries),
            ended_at=max(entry.timestamp for entry in self.entries),
            event_count=len(self.entries),
        )

    @property
    def events(self) -> tuple[OperationsEvent, ...]:
        return tuple(entry.event_payload for entry in self.entries)
