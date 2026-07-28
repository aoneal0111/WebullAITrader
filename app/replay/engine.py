from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from threading import RLock

from app.operations_core import OperationsBus, OperationsEvent

from .archive import ReplayEventArchive
from .clock import ReplayClock
from .models import ReplaySpeed, ReplayStatus


ReplayResetSink = Callable[
    [tuple[OperationsEvent, ...]],
    OperationsBus,
]


class ReplayEngine:
    """Deterministically publish an archive into a dedicated event bus."""

    def __init__(
        self,
        bus: OperationsBus,
        clock: ReplayClock,
        *,
        reset_sink: ReplayResetSink,
    ) -> None:
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be an OperationsBus")
        if not isinstance(clock, ReplayClock):
            raise TypeError("clock must be a ReplayClock")
        if not callable(reset_sink):
            raise TypeError("reset_sink must be callable")
        self._lock = RLock()
        self._bus = bus
        self._clock = clock
        self._reset_sink = reset_sink
        self._archive = ReplayEventArchive()
        self._event_index = 0
        self._status = ReplayStatus.EMPTY
        self._closed = False

    @property
    def bus(self) -> OperationsBus:
        with self._lock:
            return self._bus

    @property
    def archive(self) -> ReplayEventArchive:
        with self._lock:
            return self._archive

    @property
    def event_index(self) -> int:
        with self._lock:
            return self._event_index

    @property
    def status(self) -> ReplayStatus:
        with self._lock:
            return self._status

    def load(self, archive: ReplayEventArchive) -> None:
        if not isinstance(archive, ReplayEventArchive):
            raise TypeError("archive must be a ReplayEventArchive")
        if not archive.entries:
            raise ValueError("archive must contain at least one event")
        with self._lock:
            self._ensure_open()
            self._archive = archive
            self._event_index = 0
            self._clock.reset()
            self._replace_projection(())
            self._status = ReplayStatus.READY

    def play(self) -> None:
        with self._lock:
            self._ensure_loaded()
            if self._event_index == len(self._archive.entries):
                self.seek(0)
            self._clock.resume()
            self._status = ReplayStatus.PLAYING

    def pause(self) -> None:
        with self._lock:
            self._ensure_loaded()
            self._clock.pause()
            if self._status is not ReplayStatus.COMPLETED:
                self._status = ReplayStatus.PAUSED

    def resume(self) -> None:
        self.play()

    def stop(self) -> None:
        with self._lock:
            self._ensure_loaded()
            self._clock.reset()
            self._event_index = 0
            self._replace_projection(())
            self._status = ReplayStatus.STOPPED

    def set_speed(self, speed: ReplaySpeed) -> None:
        with self._lock:
            self._ensure_loaded()
            self._clock.set_speed(speed)
            if speed is ReplaySpeed.PAUSED:
                self._status = ReplayStatus.PAUSED

    def seek(self, event_index: int) -> None:
        with self._lock:
            self._ensure_loaded()
            if (
                isinstance(event_index, bool)
                or not isinstance(event_index, int)
                or not 0 <= event_index <= len(self._archive.entries)
            ):
                raise ValueError(
                    "event_index must identify an archive boundary"
                )
            was_playing = self._status is ReplayStatus.PLAYING
            if event_index < self._event_index:
                prefix = self._archive.events[:event_index]
                self._replace_projection(prefix)
                self._event_index = event_index
            elif event_index > self._event_index:
                self._publish_until_index(event_index)
            self._clock.seek(self._elapsed_at_position(event_index))
            if event_index == len(self._archive.entries):
                self._clock.pause()
                self._status = ReplayStatus.COMPLETED
            elif was_playing:
                self._status = ReplayStatus.PLAYING
            else:
                self._clock.pause()
                self._status = ReplayStatus.PAUSED

    def step_forward(self) -> OperationsEvent | None:
        with self._lock:
            self._ensure_loaded()
            if self._event_index == len(self._archive.entries):
                return None
            entry = self._archive.entries[self._event_index]
            self._bus.publish(entry.event_payload)
            self._event_index += 1
            self._clock.seek(
                self._elapsed_at_position(self._event_index)
            )
            self._clock.pause()
            self._status = (
                ReplayStatus.COMPLETED
                if self._event_index == len(self._archive.entries)
                else ReplayStatus.PAUSED
            )
            return entry.event_payload

    def step_backward(self) -> OperationsEvent | None:
        with self._lock:
            self._ensure_loaded()
            if self._event_index == 0:
                return None
            removed = self._archive.entries[
                self._event_index - 1
            ].event_payload
            self.seek(self._event_index - 1)
            return removed

    def advance(self, elapsed_seconds: Decimal) -> int:
        with self._lock:
            self._ensure_loaded()
            if self._status is not ReplayStatus.PLAYING:
                return 0
            target_elapsed = self._clock.advance(elapsed_seconds)
            start_index = self._event_index
            first_timestamp = self._archive.entries[0].timestamp
            while self._event_index < len(self._archive.entries):
                entry = self._archive.entries[self._event_index]
                offset = Decimal(
                    str(
                        (
                            entry.timestamp - first_timestamp
                        ).total_seconds()
                    )
                )
                if offset > target_elapsed:
                    break
                self._bus.publish(entry.event_payload)
                self._event_index += 1
            if self._event_index == len(self._archive.entries):
                self._clock.pause()
                self._status = ReplayStatus.COMPLETED
            return self._event_index - start_index

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._clock.pause()
            self._closed = True

    def _publish_until_index(self, target: int) -> None:
        while self._event_index < target:
            entry = self._archive.entries[self._event_index]
            self._bus.publish(entry.event_payload)
            self._event_index += 1

    def _replace_projection(
        self,
        prefix: tuple[OperationsEvent, ...],
    ) -> None:
        replacement = self._reset_sink(prefix)
        if not isinstance(replacement, OperationsBus):
            raise TypeError("reset_sink must return an OperationsBus")
        self._bus = replacement

    def _elapsed_at_position(self, event_index: int) -> Decimal:
        if event_index == 0:
            return Decimal("0")
        first = self._archive.entries[0].timestamp
        current = self._archive.entries[event_index - 1].timestamp
        return Decimal(str((current - first).total_seconds()))

    def _ensure_loaded(self) -> None:
        self._ensure_open()
        if not self._archive.entries:
            raise RuntimeError("no replay archive is loaded")

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("replay engine is closed")
