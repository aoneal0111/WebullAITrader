from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from app.analytics import AnalyticsController
from app.event_store import EventStoreController
from app.operations_core import (
    OperationsBus,
    RuntimeFailed,
    RuntimeStarted,
    RuntimeStarting,
    RuntimeStopped,
)
from app.recording import SessionRecorder

from .market_feed import HistoricalMarketFeed
from .models import Experiment, ExperimentResult, PlaybackStatus
from .playback_engine import PlaybackEngine


class ExperimentRunner:
    """Orchestrate production playback and consume existing recorded projections."""

    def __init__(
        self,
        playback_engine: PlaybackEngine,
        bus: OperationsBus,
        session_recorder: SessionRecorder,
        event_store_controller: EventStoreController,
        analytics_controller: AnalyticsController,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(playback_engine, PlaybackEngine):
            raise TypeError("playback_engine must be PlaybackEngine")
        if not isinstance(bus, OperationsBus):
            raise TypeError("bus must be OperationsBus")
        if not isinstance(session_recorder, SessionRecorder):
            raise TypeError("session_recorder must be SessionRecorder")
        if not isinstance(event_store_controller, EventStoreController):
            raise TypeError("event_store_controller must be EventStoreController")
        if not isinstance(analytics_controller, AnalyticsController):
            raise TypeError("analytics_controller must be AnalyticsController")
        self._playback = playback_engine
        self._bus = bus
        self._recorder = session_recorder
        self._event_store = event_store_controller
        self._analytics = analytics_controller
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run(
        self,
        experiment: Experiment,
        feed: HistoricalMarketFeed,
    ) -> ExperimentResult:
        if not isinstance(experiment, Experiment):
            raise TypeError("experiment must be Experiment")
        if not isinstance(feed, HistoricalMarketFeed):
            raise TypeError("feed must implement HistoricalMarketFeed")
        if feed.event_count <= 0:
            raise ValueError("experiment feed must contain events")
        configuration = experiment.configuration
        self._playback.set_speed(configuration.speed)
        self._playback.load(
            feed,
            start_time=configuration.start_time,
            end_time=configuration.end_time,
        )
        snapshot = self._playback.snapshot()
        if snapshot.event_count == 0:
            raise ValueError("experiment time range contains no events")
        events = feed.events()
        started_at = next(
            event.timestamp
            for event in events
            if configuration.start_time is None
            or event.timestamp >= configuration.start_time
        )
        ended_at = tuple(
            event.timestamp
            for event in events
            if (
                configuration.start_time is None
                or event.timestamp >= configuration.start_time
            )
            and (
                configuration.end_time is None
                or event.timestamp <= configuration.end_time
            )
        )[-1]
        self._bus.publish(
            RuntimeStarting(
                environment="HISTORICAL",
                occurred_at=started_at,
            )
        )
        self._bus.publish(
            RuntimeStarted(
                environment="HISTORICAL",
                active_model=configuration.strategy_version,
                occurred_at=started_at,
            )
        )
        playback = self._playback.start()
        if playback.status is PlaybackStatus.COMPLETED:
            self._bus.publish(
                RuntimeStopped(
                    reason="Historical experiment completed",
                    cycles_completed=playback.position,
                    occurred_at=ended_at,
                )
            )
        else:
            self._bus.publish(
                RuntimeFailed(
                    error_message=playback.error or "Historical playback failed",
                    occurred_at=(
                        playback.current_timestamp or ended_at
                    ),
                )
            )
        session = self._recorder.completed_session()
        self._event_store.refresh()
        analytics = self._analytics.refresh()
        return ExperimentResult(
            experiment=experiment,
            playback_status=playback.status,
            started_at=started_at,
            ended_at=playback.current_timestamp or started_at,
            processed_event_count=playback.position,
            recorded_session_id=(
                session.session_id if session is not None else None
            ),
            analytics=analytics,
            completed_at=self._clock(),
            error=playback.error,
        )

    def close(self) -> None:
        return None
