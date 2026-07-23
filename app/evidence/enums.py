from __future__ import annotations

from enum import StrEnum


class EvidenceCategory(StrEnum):
    TECHNICAL = "technical"
    MOMENTUM = "momentum"
    NEWS = "news"
    OPTIONS = "options"
    FUNDAMENTAL = "fundamental"
    MARKET = "market"
    RISK = "risk"
    AI = "ai"


class SignalDirection(StrEnum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"
    EXIT_LONG = "exit_long"
    EXIT_SHORT = "exit_short"
    NO_ACTION = "no_action"

    @property
    def polarity(self) -> int:
        if self in {SignalDirection.LONG, SignalDirection.EXIT_SHORT}:
            return 1

        if self in {SignalDirection.SHORT, SignalDirection.EXIT_LONG}:
            return -1

        return 0
