from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


MAX_TIMELINE_ENTRIES = 500


class TimelineCategory(StrEnum):
    SYSTEM = "SYSTEM"
    SCANNER = "SCANNER"
    EVIDENCE = "EVIDENCE"
    COMMITTEE = "COMMITTEE"
    DECISION = "DECISION"
    ORDER = "ORDER"
    FILL = "FILL"
    POSITION = "POSITION"
    RISK = "RISK"
    EXIT = "EXIT"
    WARNING = "WARNING"
    ERROR = "ERROR"


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
    title: str
    description: str
    cycle: int | None = None
    symbol: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.timestamp, datetime)
            or self.timestamp.tzinfo is None
        ):
            raise ValueError("timestamp must be timezone-aware")
        if not isinstance(self.category, TimelineCategory):
            raise TypeError("category must be a TimelineCategory")
        if not isinstance(self.severity, TimelineSeverity):
            raise TypeError("severity must be a TimelineSeverity")
        for field_name in ("title", "description"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(
                    f"{field_name} must be stripped non-empty text"
                )
        if self.cycle is not None and (
            isinstance(self.cycle, bool)
            or not isinstance(self.cycle, int)
            or self.cycle < 0
        ):
            raise ValueError("cycle must be a nonnegative integer or None")
        if self.symbol is not None:
            if (
                not isinstance(self.symbol, str)
                or not self.symbol.strip()
                or self.symbol != self.symbol.strip()
            ):
                raise ValueError(
                    "symbol must be stripped non-empty text or None"
                )
            if self.symbol != self.symbol.upper():
                raise ValueError("symbol must be uppercase")


@dataclass(frozen=True, slots=True)
class TimelineSnapshot:
    entries: tuple[TimelineEntry, ...] = ()
    max_entries: int = MAX_TIMELINE_ENTRIES

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
        if (
            isinstance(self.max_entries, bool)
            or not isinstance(self.max_entries, int)
            or self.max_entries <= 0
        ):
            raise ValueError("max_entries must be a positive integer")
        if self.max_entries > MAX_TIMELINE_ENTRIES:
            raise ValueError("max_entries cannot exceed 500")
        if len(self.entries) > self.max_entries:
            raise ValueError("entries cannot exceed max_entries")

    @classmethod
    def initial(
        cls,
        *,
        max_entries: int = MAX_TIMELINE_ENTRIES,
    ) -> "TimelineSnapshot":
        return cls(max_entries=max_entries)


TimelineReadModelSnapshot = TimelineSnapshot
