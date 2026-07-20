from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ExecutionEventType(StrEnum):
    SUBMITTED = "SUBMITTED"; ACKNOWLEDGED = "ACKNOWLEDGED"; PARTIAL_FILL = "PARTIAL_FILL"
    FILL = "FILL"; CANCELLED = "CANCELLED"; REJECTED = "REJECTED"; REPLACED = "REPLACED"
    SYNCHRONIZATION_MISMATCH = "SYNCHRONIZATION_MISMATCH"


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    sequence: int
    event_type: ExecutionEventType
    request_id: str
    timestamp: datetime
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionEventLog:
    events: tuple[ExecutionEvent, ...] = ()


def append_event(log, event_type, request_id, timestamp, details=()):
    if timestamp.tzinfo is None: raise ValueError("event timestamp must be timezone-aware")
    return ExecutionEventLog((*log.events, ExecutionEvent(len(log.events) + 1, event_type, request_id,
                                                           timestamp, tuple(sorted(details)))))
