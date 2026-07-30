from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from time import perf_counter

from app.composition.runtime_projection_pipeline import (
    RuntimeProjectionPipeline,
    create_runtime_projection_pipeline,
)
from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import (
    ApplicationState,
    ApplicationStateStore,
    OperationsBus,
)

from .control import (
    ImmediateReplaySpeed,
    ReplayControl,
    ReplayProgressSink,
    ReplaySpeedControl,
)
from .models import (
    ReplayProgress,
    ReplayResult,
    ReplayStatistics,
    ReplayStatus,
)
from .source import RuntimeEventSource


class ReplayEngine:
    """Rebuild runtime projections through the production event pipeline."""

    def __init__(
        self,
        events: RuntimeEventSource | Sequence[PaperRuntimeEvent],
        *,
        account_id: str = "replay",
        timeline_history_limit: int = 500,
        watchlist_maximum_symbols: int = 100,
        watchlist_stale_after: timedelta = timedelta(seconds=30),
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        source_reader = getattr(events, "read_events", None)
        if callable(source_reader):
            recorded_events = source_reader()
        elif isinstance(events, Sequence):
            recorded_events = events
        else:
            raise TypeError(
                "events must be a RuntimeEventSource or event sequence"
            )
        if not isinstance(recorded_events, Sequence):
            raise TypeError("RuntimeEventSource must return a sequence")
        if any(
            not isinstance(event, PaperRuntimeEvent)
            for event in recorded_events
        ):
            raise TypeError("events must contain PaperRuntimeEvent instances")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._events = tuple(
            event
            for _, event in sorted(
                enumerate(recorded_events),
                key=lambda item: (
                    item[1].timestamp,
                    item[1].sequence,
                    item[1].source,
                    item[0],
                ),
            )
        )
        self._account_id = account_id
        self._timeline_history_limit = timeline_history_limit
        self._watchlist_maximum_symbols = watchlist_maximum_symbols
        self._watchlist_stale_after = watchlist_stale_after
        self._clock = clock
        self._store: ApplicationStateStore | None = None
        self._pipeline: RuntimeProjectionPipeline | None = None
        self._cursor = 0
        self._reset_pipeline()

    @property
    def ordered_events(self) -> tuple[PaperRuntimeEvent, ...]:
        return self._events

    @property
    def state(self) -> ApplicationState:
        assert self._store is not None
        return self._store.snapshot()

    @property
    def pipeline(self) -> RuntimeProjectionPipeline:
        assert self._pipeline is not None
        return self._pipeline

    @property
    def next_index(self) -> int:
        return self._cursor

    def replay(
        self,
        *,
        start_index: int = 0,
        to_timestamp: datetime | None = None,
        speed: ReplaySpeedControl | None = None,
        control: ReplayControl | None = None,
        progress: ReplayProgressSink | None = None,
    ) -> ReplayResult:
        """Replay from an arbitrary ordered event index into fresh state."""

        self._validate_index(start_index)
        self._validate_timestamp(to_timestamp)
        self._reset_pipeline()
        self._cursor = start_index
        return self._run(
            start_index=start_index,
            to_timestamp=to_timestamp,
            speed=speed,
            control=control,
            progress=progress,
            maximum_events=None,
        )

    def replay_from_beginning(
        self,
        *,
        to_timestamp: datetime | None = None,
        speed: ReplaySpeedControl | None = None,
        control: ReplayControl | None = None,
        progress: ReplayProgressSink | None = None,
    ) -> ReplayResult:
        return self.replay(
            start_index=0,
            to_timestamp=to_timestamp,
            speed=speed,
            control=control,
            progress=progress,
        )

    def resume(
        self,
        *,
        to_timestamp: datetime | None = None,
        speed: ReplaySpeedControl | None = None,
        control: ReplayControl | None = None,
        progress: ReplayProgressSink | None = None,
    ) -> ReplayResult:
        """Continue the current pipeline from the next unprocessed event."""

        self._validate_timestamp(to_timestamp)
        return self._run(
            start_index=self._cursor,
            to_timestamp=to_timestamp,
            speed=speed,
            control=control,
            progress=progress,
            maximum_events=None,
        )

    def step(
        self,
        *,
        to_timestamp: datetime | None = None,
        speed: ReplaySpeedControl | None = None,
        control: ReplayControl | None = None,
        progress: ReplayProgressSink | None = None,
    ) -> ReplayResult:
        """Replay at most one event through the current pipeline."""

        self._validate_timestamp(to_timestamp)
        return self._run(
            start_index=self._cursor,
            to_timestamp=to_timestamp,
            speed=speed,
            control=control,
            progress=progress,
            maximum_events=1,
        )

    def reset(self) -> ApplicationState:
        """Replace all projections with fresh production instances."""

        self._reset_pipeline()
        return self.state

    def verify_determinism(self) -> bool:
        """Replay twice from fresh state and compare complete snapshots."""

        first = self.replay_from_beginning().state
        second = self.replay_from_beginning().state
        return first == second

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None
        self._pipeline = None

    def _run(
        self,
        *,
        start_index: int,
        to_timestamp: datetime | None,
        speed: ReplaySpeedControl | None,
        control: ReplayControl | None,
        progress: ReplayProgressSink | None,
        maximum_events: int | None,
    ) -> ReplayResult:
        pacer = speed or ImmediateReplaySpeed()
        if not callable(getattr(pacer, "pace", None)):
            raise TypeError("speed must implement pace")
        interruption = control or ReplayControl()
        if progress is not None and not callable(progress):
            raise TypeError("progress must be callable")
        previous = (
            self._events[self._cursor - 1]
            if self._cursor > 0
            else None
        )
        interrupted = False
        processed = 0
        started_at = self._clock()
        self._report_progress(progress)

        while self._cursor < len(self._events):
            if maximum_events is not None and processed >= maximum_events:
                break
            event = self._events[self._cursor]
            if to_timestamp is not None and event.timestamp > to_timestamp:
                break
            if interruption.interrupted:
                interrupted = True
                break
            pacer.pace(previous, event)
            if interruption.interrupted:
                interrupted = True
                break
            self.pipeline.sink(event)
            previous = event
            self._cursor += 1
            processed += 1
            self._report_progress(progress)

        elapsed_seconds = max(0.0, self._clock() - started_at)
        if not self._events:
            status = ReplayStatus.EMPTY
        elif interrupted:
            status = ReplayStatus.INTERRUPTED
        elif self._cursor < len(self._events):
            status = ReplayStatus.PARTIAL
        else:
            status = ReplayStatus.COMPLETED
        return ReplayResult(
            status=status,
            start_index=start_index,
            next_index=self._cursor,
            processed_events=processed,
            total_events=len(self._events),
            state=self.state,
            progress=ReplayProgress(
                current_event=self._cursor,
                total_events=len(self._events),
            ),
            statistics=ReplayStatistics(
                events_processed=processed,
                elapsed_seconds=elapsed_seconds,
                processing_rate=(
                    processed / elapsed_seconds
                    if elapsed_seconds > 0
                    else 0.0
                ),
            ),
        )

    def _report_progress(
        self,
        progress: ReplayProgressSink | None,
    ) -> None:
        if progress is not None:
            progress(
                ReplayProgress(
                    current_event=self._cursor,
                    total_events=len(self._events),
                )
            )

    def _reset_pipeline(self) -> None:
        if self._store is not None:
            self._store.close()
        bus = OperationsBus()
        self._store = ApplicationStateStore(bus)
        self._pipeline = create_runtime_projection_pipeline(
            operations_bus=bus,
            account_id=self._account_id,
            timeline_history_limit=self._timeline_history_limit,
            watchlist_maximum_symbols=self._watchlist_maximum_symbols,
            watchlist_stale_after=self._watchlist_stale_after,
        )
        self._cursor = 0

    def _validate_index(self, start_index: int) -> None:
        if (
            isinstance(start_index, bool)
            or not isinstance(start_index, int)
            or not 0 <= start_index <= len(self._events)
        ):
            raise ValueError("start_index is outside the event stream")

    @staticmethod
    def _validate_timestamp(value: datetime | None) -> None:
        if value is not None and (
            not isinstance(value, datetime)
            or value.tzinfo is None
        ):
            raise ValueError("to_timestamp must be timezone-aware")
