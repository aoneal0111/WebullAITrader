from __future__ import annotations

from threading import RLock
from typing import Protocol

from .snapshot_models import CandleSeriesSnapshot


class SnapshotSubscriber(Protocol):
    """Receives immutable candle-series snapshots from a publisher."""

    def on_snapshot(self, snapshot: CandleSeriesSnapshot) -> None:
        """Handle one published snapshot."""


class SnapshotPublisher:
    """Thread-safe, ordered distributor for candle-series snapshots.

    Subscriber callbacks execute in subscription order. The subscriber list is
    copied while holding the lock, then callbacks run outside the lock so a
    subscriber may safely subscribe or unsubscribe during publication.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._subscribers: list[SnapshotSubscriber] = []

    def subscribe(self, subscriber: SnapshotSubscriber) -> bool:
        """Register a subscriber once and return whether it was added."""
        self._validate_subscriber(subscriber)
        with self._lock:
            if any(existing is subscriber for existing in self._subscribers):
                return False
            self._subscribers.append(subscriber)
            return True

    def unsubscribe(self, subscriber: SnapshotSubscriber) -> bool:
        """Remove a subscriber and return whether it was registered."""
        with self._lock:
            for index, existing in enumerate(self._subscribers):
                if existing is subscriber:
                    del self._subscribers[index]
                    return True
            return False

    def publish(self, snapshot: CandleSeriesSnapshot) -> None:
        """Publish a snapshot to the subscribers present at call time."""
        if not isinstance(snapshot, CandleSeriesSnapshot):
            raise TypeError("snapshot must be CandleSeriesSnapshot")

        with self._lock:
            subscribers = tuple(self._subscribers)

        for subscriber in subscribers:
            subscriber.on_snapshot(snapshot)

    @property
    def subscriber_count(self) -> int:
        """Return the number of currently registered subscribers."""
        with self._lock:
            return len(self._subscribers)

    @staticmethod
    def _validate_subscriber(subscriber: SnapshotSubscriber) -> None:
        callback = getattr(subscriber, "on_snapshot", None)
        if not callable(callback):
            raise TypeError("subscriber must define callable on_snapshot(snapshot)")
