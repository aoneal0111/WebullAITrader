from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.analytics import AnalyticsSnapshot
from app.backtesting.models import (
    BacktestConfiguration,
    ComparisonSnapshot,
    Experiment,
    ExperimentResult,
    ExperimentSnapshot,
    PlaybackSnapshot,
    PlaybackStatus,
)

NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def test_playback_and_experiment_models_are_frozen_and_slotted() -> None:
    playback = PlaybackSnapshot.initial()
    snapshot = ExperimentSnapshot.initial()
    assert playback.status is PlaybackStatus.EMPTY
    assert not hasattr(snapshot, "__dict__")
    with pytest.raises(FrozenInstanceError):
        playback.position = 1


@pytest.mark.parametrize(
    "factory",
    (
        lambda: PlaybackSnapshot(
            PlaybackStatus.READY, 2, 1, Decimal("1")
        ),
        lambda: PlaybackSnapshot(
            PlaybackStatus.READY, 0, 1, Decimal("0")
        ),
        lambda: BacktestConfiguration("v1", start_time=datetime(2026, 1, 1)),
        lambda: BacktestConfiguration(
            "v1", start_time=NOW, end_time=NOW.replace(year=2025)
        ),
        lambda: Experiment("", "name", BacktestConfiguration("v1")),
        lambda: ExperimentSnapshot(
            PlaybackSnapshot.initial(),
            (),
            "missing",
            ComparisonSnapshot.initial(),
        ),
    ),
)
def test_playback_model_validation(factory) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_experiment_result_validates_analytics_contract() -> None:
    experiment = Experiment("one", "One", BacktestConfiguration("v1"))
    with pytest.raises(TypeError, match="analytics"):
        ExperimentResult(
            experiment,
            PlaybackStatus.COMPLETED,
            NOW,
            NOW,
            0,
            "session",
            object(),
            NOW,
        )
    assert AnalyticsSnapshot.initial().performance.total_trades == 0
