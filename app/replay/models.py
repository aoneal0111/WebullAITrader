from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ReplayState(StrEnum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"


class ReplayStatus(StrEnum):
    EMPTY = "EMPTY"
    READY = "READY"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"


class ReplaySpeed(StrEnum):
    PAUSED = "PAUSED"
    X1 = "1X"
    X2 = "2X"
    X5 = "5X"
    X10 = "10X"
    X20 = "20X"

    @property
    def multiplier(self) -> Decimal:
        return {
            ReplaySpeed.PAUSED: Decimal("0"),
            ReplaySpeed.X1: Decimal("1"),
            ReplaySpeed.X2: Decimal("2"),
            ReplaySpeed.X5: Decimal("5"),
            ReplaySpeed.X10: Decimal("10"),
            ReplaySpeed.X20: Decimal("20"),
        }[self]


@dataclass(frozen=True, slots=True)
class ReplaySession:
    session_id: str
    started_at: datetime
    ended_at: datetime
    event_count: int

    def __post_init__(self) -> None:
        _validate_text(self.session_id, "session_id")
        _validate_timestamp(self.started_at, "started_at")
        _validate_timestamp(self.ended_at, "ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        if (
            isinstance(self.event_count, bool)
            or not isinstance(self.event_count, int)
            or self.event_count <= 0
        ):
            raise ValueError("event_count must be a positive integer")


@dataclass(frozen=True, slots=True)
class ReplayPosition:
    event_index: int = 0
    total_events: int = 0
    sequence_number: int | None = None
    timestamp: datetime | None = None
    progress: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.event_index, "event_index"),
            (self.total_events, "total_events"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(
                    f"{field_name} must be a nonnegative integer"
                )
        if self.event_index > self.total_events:
            raise ValueError("event_index cannot exceed total_events")
        if self.sequence_number is not None and (
            isinstance(self.sequence_number, bool)
            or not isinstance(self.sequence_number, int)
            or self.sequence_number <= 0
        ):
            raise ValueError(
                "sequence_number must be a positive integer or None"
            )
        if self.timestamp is not None:
            _validate_timestamp(self.timestamp, "timestamp")
        if (
            not isinstance(self.progress, Decimal)
            or not self.progress.is_finite()
            or not Decimal("0") <= self.progress <= Decimal("100")
        ):
            raise ValueError(
                "progress must be a finite Decimal from 0 to 100"
            )
        if self.event_index == 0 and (
            self.sequence_number is not None
            or self.timestamp is not None
        ):
            raise ValueError(
                "initial position cannot identify a replayed event"
            )
        if self.event_index > 0 and (
            self.sequence_number is None
            or self.timestamp is None
        ):
            raise ValueError(
                "advanced position must identify the replayed event"
            )


@dataclass(frozen=True, slots=True)
class ReplaySnapshot:
    session: ReplaySession | None
    state: ReplayState
    status: ReplayStatus
    position: ReplayPosition
    speed: ReplaySpeed

    def __post_init__(self) -> None:
        if self.session is not None and not isinstance(
            self.session,
            ReplaySession,
        ):
            raise TypeError("session must be a ReplaySession or None")
        if not isinstance(self.state, ReplayState):
            raise TypeError("state must be a ReplayState")
        if not isinstance(self.status, ReplayStatus):
            raise TypeError("status must be a ReplayStatus")
        if not isinstance(self.position, ReplayPosition):
            raise TypeError("position must be a ReplayPosition")
        if not isinstance(self.speed, ReplaySpeed):
            raise TypeError("speed must be a ReplaySpeed")
        if self.state is ReplayState.LIVE:
            if self.session is not None:
                raise ValueError("LIVE state cannot contain a replay session")
            if self.status is not ReplayStatus.EMPTY:
                raise ValueError("LIVE state must have EMPTY status")
        if self.state is ReplayState.REPLAY:
            if self.session is None:
                raise ValueError("REPLAY state requires a session")
            if self.position.total_events != self.session.event_count:
                raise ValueError(
                    "position total must match session event count"
                )

    @classmethod
    def initial(cls) -> "ReplaySnapshot":
        return cls(
            session=None,
            state=ReplayState.LIVE,
            status=ReplayStatus.EMPTY,
            position=ReplayPosition(),
            speed=ReplaySpeed.PAUSED,
        )


def _validate_text(value: str, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
    ):
        raise ValueError(f"{field_name} must be stripped non-empty text")


def _validate_timestamp(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
