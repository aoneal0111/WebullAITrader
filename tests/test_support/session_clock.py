"""Timezone-aware clocks for tests that require a valid Atlas DAY."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from app.paper_trading.command_composition import (
    create_paper_trading_command_composition as _create_paper_composition,
)


EASTERN = ZoneInfo("America/New_York")
REGULAR_SESSION_ET = datetime(2026, 9, 3, 14, 0, tzinfo=EASTERN)
REGULAR_SESSION_UTC = REGULAR_SESSION_ET.astimezone(UTC)
AFTER_HOURS_ET = datetime(2026, 9, 3, 18, 0, tzinfo=EASTERN)
AFTER_HOURS_UTC = AFTER_HOURS_ET.astimezone(UTC)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fixed test clock must be timezone-aware")
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def create_session_paper_composition(
    *,
    at: datetime = REGULAR_SESSION_UTC,
    clock: Callable[[], datetime] | None = None,
    **kwargs,
):
    """Create PAPER commands inside a deterministic supported Atlas DAY."""

    return _create_paper_composition(
        clock=clock or FixedClock(at),
        **kwargs,
    )


def session_timestamp(
    sequence: int = 0,
    *,
    at: datetime = REGULAR_SESSION_UTC,
) -> datetime:
    """Return a stable, ordered event time within the chosen session."""

    return at + timedelta(seconds=sequence)


__all__ = [
    "AFTER_HOURS_ET",
    "AFTER_HOURS_UTC",
    "EASTERN",
    "FixedClock",
    "REGULAR_SESSION_ET",
    "REGULAR_SESSION_UTC",
    "create_session_paper_composition",
    "session_timestamp",
]
