from __future__ import annotations

from app.market_data.candle_aggregator import CandleAggregator
from app.market_data.candle_models import Candle
from app.market_data.models import MarketEvent
from app.market_data.snapshot_models import CandleSeriesSnapshot
from app.market_data.snapshot_publisher import SnapshotPublisher


class CandleSnapshotRuntime:
    """Convert canonical trade events into immutable published candle snapshots."""

    def __init__(
        self,
        aggregator: CandleAggregator,
        publisher: SnapshotPublisher,
    ) -> None:
        if not isinstance(aggregator, CandleAggregator):
            raise TypeError("aggregator must be CandleAggregator")
        if not isinstance(publisher, SnapshotPublisher):
            raise TypeError("publisher must be SnapshotPublisher")

        self._aggregator = aggregator
        self._publisher = publisher
        self._completed: list[Candle] = []
        self._sequence = 0

    @property
    def sequence(self) -> int:
        """Return the most recently published snapshot sequence."""

        return self._sequence

    @property
    def completed(self) -> tuple[Candle, ...]:
        """Return the immutable completed-candle history."""

        return tuple(self._completed)

    def on_event(self, event: MarketEvent) -> CandleSeriesSnapshot:
        """Aggregate one trade event and publish the resulting series snapshot."""

        completed = self._aggregator.on_event(event)
        if completed is not None:
            self._completed.append(completed)

        current = self._aggregator.current_candle
        symbol = self._aggregator.symbol
        if symbol is None or current is None:
            raise RuntimeError("aggregator did not produce a current candle")

        self._sequence += 1
        snapshot = CandleSeriesSnapshot(
            symbol=symbol,
            interval=self._aggregator.interval,
            sequence=self._sequence,
            created_at=event.timestamp,
            completed=tuple(self._completed),
            current=current,
        )
        self._publisher.publish(snapshot)
        return snapshot
