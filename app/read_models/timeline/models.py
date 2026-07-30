from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TimelineCategory(StrEnum):
    RUNTIME = "RUNTIME"
    BROKER = "BROKER"
    MARKET_DATA = "MARKET_DATA"
    AI = "AI"
    ORDER = "ORDER"
    EXECUTION = "EXECUTION"
    SYSTEM = "SYSTEM"


class TimelineSeverity(StrEnum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    timestamp: datetime
    category: TimelineCategory
    severity: TimelineSeverity
    source: str
    title: str
    description: str
    related_symbol: str | None = None
    related_order_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        if not isinstance(self.category, TimelineCategory):
            raise TypeError("category must be a TimelineCategory")
        if not isinstance(self.severity, TimelineSeverity):
            raise TypeError("severity must be a TimelineSeverity")

        for field_name in ("source", "title", "description"):
            _required_text(getattr(self, field_name), field_name)
        for field_name in ("related_symbol", "related_order_id"):
            value = getattr(self, field_name)
            if value is not None:
                _required_text(value, field_name)


@dataclass(frozen=True, slots=True)
class TimelineReadModelSnapshot:
    entries: tuple[TimelineEntry, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple):
            raise TypeError("entries must be an immutable tuple")
        if any(
            not isinstance(entry, TimelineEntry)
            for entry in self.entries
        ):
            raise TypeError(
                "entries must contain only TimelineEntry instances"
            )
        if any(
            first.timestamp < second.timestamp
            for first, second in zip(
                self.entries,
                self.entries[1:],
            )
        ):
            raise ValueError("timeline entries must be newest-first")

    @classmethod
    def initial(cls) -> "TimelineReadModelSnapshot":
        return cls()


def _required_text(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if value != value.strip():
        raise ValueError(f"{field_name} must be stripped")
