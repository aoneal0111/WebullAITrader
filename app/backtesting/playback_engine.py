from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from threading import RLock

from app.market_data import MarketEvent

from .market_feed import HistoricalMarketFeed
from .models import PlaybackSnapshot, PlaybackStatus


MarketEventSink = Callable[[MarketEvent], object]
PlaybackListener = Callable[[PlaybackSnapshot], None]


class PlaybackEngine:
    """Deterministically emit historical events to a production event sink."""

    def __init__(self, event_sink: MarketEventSink) -> None:
        if not callable(event_sink):
            raise TypeError("event_sink must be callable")
        self._event_sink = event_sink
        self._events: tuple[MarketEvent, ...] = ()
        self._position = 0
        self._speed = Decimal("1")
        self._status = PlaybackStatus.EMPTY
        self._current_timestamp: datetime | None = None
        self._error: str | None = None
        self._listeners: dict[int, PlaybackListener] = {}
        self._next_listener_id = 1
        self._lock = RLock()

    def load(
        self,
        feed: HistoricalMarketFeed,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> PlaybackSnapshot:
        self._ensure_not_closed()
        if not isinstance(feed, HistoricalMarketFeed):
            raise TypeError("feed must implement HistoricalMarketFeed")
        _optional_timestamp(start_time, "start_time")
        _optional_timestamp(end_time, "end_time")
        if (
            start_time is not None
            and end_time is not None
            and end_time < start_time
        ):
            raise ValueError("end_time cannot precede start_time")
        values = tuple(
            event
            for event in feed.events()
            if (start_time is None or event.timestamp >= start_time)
            and (end_time is None or event.timestamp <= end_time)
        )
        with self._lock:
            self._events = values
            self._position = 0
            self._current_timestamp = None
            self._error = None
            self._status = (
                PlaybackStatus.READY
                if values
                else PlaybackStatus.EMPTY
            )
        return self._changed()

    def start(self) -> PlaybackSnapshot:
        self._require_status(
            PlaybackStatus.READY,
            PlaybackStatus.PAUSED,
            PlaybackStatus.STOPPED,
        )
        with self._lock:
            self._status = PlaybackStatus.RUNNING
        self._changed()
        while self.snapshot().status is PlaybackStatus.RUNNING:
            if self.snapshot().position >= self.snapshot().event_count:
                with self._lock:
                    self._status = PlaybackStatus.COMPLETED
                return self._changed()
            self._emit_one()
        return self.snapshot()

    def pause(self) -> PlaybackSnapshot:
        self._require_status(PlaybackStatus.RUNNING)
        with self._lock:
            self._status = PlaybackStatus.PAUSED
        return self._changed()

    def resume(self) -> PlaybackSnapshot:
        self._require_status(PlaybackStatus.PAUSED)
        return self.start()

    def step(self) -> PlaybackSnapshot:
        self._require_status(
            PlaybackStatus.READY,
            PlaybackStatus.PAUSED,
            PlaybackStatus.STOPPED,
        )
        if self.snapshot().position >= self.snapshot().event_count:
            with self._lock:
                self._status = PlaybackStatus.COMPLETED
            return self._changed()
        self._emit_one()
        with self._lock:
            self._status = (
                PlaybackStatus.COMPLETED
                if self._position == len(self._events)
                else PlaybackStatus.PAUSED
            )
        return self._changed()

    def seek(self, target: int | datetime) -> PlaybackSnapshot:
        self._require_status(
            PlaybackStatus.READY,
            PlaybackStatus.PAUSED,
            PlaybackStatus.STOPPED,
            PlaybackStatus.COMPLETED,
        )
        if isinstance(target, bool):
            raise TypeError("seek target must be an index or datetime")
        if isinstance(target, int):
            if not 0 <= target <= len(self._events):
                raise ValueError("seek position is out of range")
            position = target
        elif isinstance(target, datetime) and target.tzinfo is not None:
            position = next(
                (
                    index
                    for index, event in enumerate(self._events)
                    if event.timestamp >= target
                ),
                len(self._events),
            )
        else:
            raise TypeError("seek target must be an index or aware datetime")
        with self._lock:
            self._position = position
            self._current_timestamp = (
                self._events[position - 1].timestamp
                if position
                else None
            )
            self._status = (
                PlaybackStatus.COMPLETED
                if position == len(self._events)
                else PlaybackStatus.READY
            )
            self._error = None
        return self._changed()

    def restart(self) -> PlaybackSnapshot:
        self._require_status(
            PlaybackStatus.READY,
            PlaybackStatus.PAUSED,
            PlaybackStatus.STOPPED,
            PlaybackStatus.COMPLETED,
            PlaybackStatus.ERROR,
        )
        with self._lock:
            self._position = 0
            self._current_timestamp = None
            self._error = None
            self._status = (
                PlaybackStatus.READY
                if self._events
                else PlaybackStatus.EMPTY
            )
        return self._changed()

    def stop(self) -> PlaybackSnapshot:
        self._require_status(
            PlaybackStatus.READY,
            PlaybackStatus.RUNNING,
            PlaybackStatus.PAUSED,
        )
        with self._lock:
            self._status = PlaybackStatus.STOPPED
        return self._changed()

    def set_speed(self, speed: Decimal) -> PlaybackSnapshot:
        if not isinstance(speed, Decimal) or not speed.is_finite() or speed <= 0:
            raise ValueError("speed must be a positive finite Decimal")
        self._ensure_not_closed()
        with self._lock:
            self._speed = speed
        return self._changed()

    def snapshot(self) -> PlaybackSnapshot:
        with self._lock:
            return PlaybackSnapshot(
                self._status,
                self._position,
                len(self._events),
                self._speed,
                self._current_timestamp,
                self._error,
            )

    def subscribe(self, listener: PlaybackListener) -> int:
        if not callable(listener):
            raise TypeError("listener must be callable")
        self._ensure_not_closed()
        with self._lock:
            identifier = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[identifier] = listener
            snapshot = self.snapshot()
        listener(snapshot)
        return identifier

    def unsubscribe(self, identifier: int) -> bool:
        with self._lock:
            return self._listeners.pop(identifier, None) is not None

    def close(self) -> None:
        with self._lock:
            if self._status is PlaybackStatus.CLOSED:
                return
            self._status = PlaybackStatus.CLOSED
            self._listeners.clear()

    def _emit_one(self) -> None:
        with self._lock:
            event = self._events[self._position]
        try:
            self._event_sink(event)
        except Exception as exc:
            with self._lock:
                self._status = PlaybackStatus.ERROR
                self._error = str(exc) or type(exc).__name__
            self._changed()
            return
        with self._lock:
            self._position += 1
            self._current_timestamp = event.timestamp
        self._changed()

    def _changed(self) -> PlaybackSnapshot:
        snapshot = self.snapshot()
        with self._lock:
            listeners = tuple(self._listeners.values())
        for listener in listeners:
            listener(snapshot)
        return snapshot

    def _require_status(self, *statuses: PlaybackStatus) -> None:
        self._ensure_not_closed()
        if self.snapshot().status not in statuses:
            expected = ", ".join(value.value for value in statuses)
            raise RuntimeError(f"playback requires state: {expected}")

    def _ensure_not_closed(self) -> None:
        if self.snapshot().status is PlaybackStatus.CLOSED:
            raise RuntimeError("playback engine is closed")


def _optional_timestamp(value: datetime | None, name: str) -> None:
    if value is not None and (
        not isinstance(value, datetime)
        or value.tzinfo is None
    ):
        raise ValueError(f"{name} must be timezone-aware or None")
