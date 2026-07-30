from __future__ import annotations

from threading import Event
from typing import Protocol

from app.operations.runtime import PaperRuntimeEvent


class ReplaySpeedControl(Protocol):
    """Pacing boundary for immediate, real-time, or UI-controlled replay."""

    def pace(
        self,
        previous_event: PaperRuntimeEvent | None,
        next_event: PaperRuntimeEvent,
    ) -> None: ...


class ImmediateReplaySpeed:
    def pace(
        self,
        previous_event: PaperRuntimeEvent | None,
        next_event: PaperRuntimeEvent,
    ) -> None:
        del previous_event, next_event


class ReplayControl:
    """Thread-safe cooperative replay interruption signal."""

    def __init__(self) -> None:
        self._interrupted = Event()

    @property
    def interrupted(self) -> bool:
        return self._interrupted.is_set()

    def interrupt(self) -> None:
        self._interrupted.set()

    def reset(self) -> None:
        self._interrupted.clear()
