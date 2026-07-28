from pathlib import Path

from app.analytics import AnalyticsStatus
from app.backtesting.market_feed import InMemoryHistoricalMarketFeed
from app.backtesting.models import (
    BacktestConfiguration,
    Experiment,
    PlaybackStatus,
)
from app.composition import create_desktop_composition
from app.composition.desktop_runtime_config import DesktopRuntimeConfiguration
from app.event_store import EventStoreStatus


def test_experiment_reuses_recording_event_store_and_analytics_path(
    tmp_path: Path,
    historical_events,
) -> None:
    composition = create_desktop_composition(
        configuration=DesktopRuntimeConfiguration(
            recording_directory=tmp_path,
        )
    )
    try:
        composition.backtesting_controller.load(
            InMemoryHistoricalMarketFeed(historical_events)
        )
        snapshot = composition.backtesting_controller.start_experiment(
            Experiment(
                "experiment-one",
                "Experiment One",
                BacktestConfiguration("strategy-v1"),
            )
        )
        result = snapshot.experiments[0]
        assert result.playback_status is PlaybackStatus.COMPLETED
        assert result.processed_event_count == 3
        assert result.recorded_session_id
        assert composition.session_recorder.completed_session() is not None
        assert composition.event_store_controller.snapshot().status is (
            EventStoreStatus.READY
        )
        assert (
            composition.event_store_controller.snapshot()
            .statistics.total_events
            == 3
        )
        assert result.analytics.status is AnalyticsStatus.EMPTY
        assert tuple(tmp_path.glob("*.atlas-session.json"))
    finally:
        composition.close(timeout_seconds=1.0)


def test_experiment_failure_is_recorded_and_propagated(
    tmp_path: Path,
    historical_events,
) -> None:
    from app.backtesting.experiment_runner import ExperimentRunner
    from app.backtesting.playback_engine import PlaybackEngine

    composition = create_desktop_composition(
        configuration=DesktopRuntimeConfiguration(
            recording_directory=tmp_path,
        )
    )
    failing = PlaybackEngine(
        lambda event: (_ for _ in ()).throw(RuntimeError("sink failure"))
    )
    runner = ExperimentRunner(
        failing,
        composition.bus,
        composition.session_recorder,
        composition.event_store_controller,
        composition.analytics_controller,
    )
    try:
        result = runner.run(
            Experiment("failed", "Failed", BacktestConfiguration("v1")),
            InMemoryHistoricalMarketFeed(historical_events),
        )
        assert result.playback_status is PlaybackStatus.ERROR
        assert result.error == "sink failure"
        assert result.processed_event_count == 0
        assert composition.session_recorder.completed_session() is not None
    finally:
        failing.close()
        composition.close(timeout_seconds=1.0)
