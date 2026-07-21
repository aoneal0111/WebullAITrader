from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LiveScannerCycle:
    events_read: int
    decisions_created: int
    stream_exhausted: bool
    running: bool


@dataclass(frozen=True, slots=True)
class LiveScannerStatus:
    connected: bool
    running: bool
    channels: tuple[str, ...]
    cycles_completed: int
    events_read: int
    decisions_created: int
