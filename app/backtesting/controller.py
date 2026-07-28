from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from .comparison import ComparisonEngine
from .experiment_runner import ExperimentRunner
from .market_feed import HistoricalMarketFeed
from .models import (
    ComparisonSnapshot,
    Experiment,
    ExperimentSnapshot,
)
from .playback_engine import PlaybackEngine
from .repository import ExperimentRepository


ExperimentListener = Callable[[ExperimentSnapshot], None]


class BacktestingController:
    def __init__(
        self,
        playback_engine: PlaybackEngine,
        runner: ExperimentRunner,
        repository: ExperimentRepository,
        comparison_engine: ComparisonEngine,
    ) -> None:
        if not isinstance(playback_engine, PlaybackEngine):
            raise TypeError("playback_engine must be PlaybackEngine")
        if not isinstance(runner, ExperimentRunner):
            raise TypeError("runner must be ExperimentRunner")
        if not isinstance(repository, ExperimentRepository):
            raise TypeError("repository must be ExperimentRepository")
        if not isinstance(comparison_engine, ComparisonEngine):
            raise TypeError("comparison_engine must be ComparisonEngine")
        self._playback = playback_engine
        self._runner = runner
        self._repository = repository
        self._comparison_engine = comparison_engine
        self._feed: HistoricalMarketFeed | None = None
        self._selected: str | None = None
        self._comparison = ComparisonSnapshot.initial()
        self._listeners: dict[int, ExperimentListener] = {}
        self._next_listener_id = 1
        self._closed = False
        self._closed_results = ()
        self._lock = RLock()

    def load(self, feed: HistoricalMarketFeed) -> ExperimentSnapshot:
        self._ensure_open()
        if not isinstance(feed, HistoricalMarketFeed):
            raise TypeError("feed must implement HistoricalMarketFeed")
        self._feed = feed
        self._playback.load(feed)
        return self._changed()

    def start_experiment(self, experiment: Experiment) -> ExperimentSnapshot:
        self._ensure_open()
        if self._feed is None:
            raise RuntimeError("historical feed is not loaded")
        result = self._runner.run(experiment, self._feed)
        self._repository.save(result)
        self._selected = experiment.experiment_id
        return self._changed()

    def pause(self) -> ExperimentSnapshot:
        self._playback.pause()
        return self._changed()

    def resume(self) -> ExperimentSnapshot:
        self._playback.resume()
        return self._changed()

    def step(self) -> ExperimentSnapshot:
        self._playback.step()
        return self._changed()

    def stop(self) -> ExperimentSnapshot:
        self._playback.stop()
        return self._changed()

    def select(self, experiment_id: str) -> ExperimentSnapshot:
        self._repository.get(experiment_id)
        self._selected = experiment_id
        return self._changed()

    def compare(
        self,
        baseline_id: str,
        candidate_id: str,
    ) -> ExperimentSnapshot:
        self._comparison = self._comparison_engine.compare(
            self._repository.get(baseline_id),
            self._repository.get(candidate_id),
        )
        return self._changed()

    def snapshot(self) -> ExperimentSnapshot:
        return ExperimentSnapshot(
            self._playback.snapshot(),
            (
                self._closed_results
                if self._closed
                else self._repository.list()
            ),
            self._selected,
            self._comparison,
        )

    def subscribe(self, listener: ExperimentListener) -> int:
        if not callable(listener):
            raise TypeError("listener must be callable")
        self._ensure_open()
        with self._lock:
            identifier = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[identifier] = listener
        listener(self.snapshot())
        return identifier

    def unsubscribe(self, identifier: int) -> bool:
        with self._lock:
            return self._listeners.pop(identifier, None) is not None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed_results = self._repository.list()
            self._closed = True
            self._listeners.clear()
        self._runner.close()
        self._playback.close()
        self._repository.close()

    def _changed(self) -> ExperimentSnapshot:
        snapshot = self.snapshot()
        with self._lock:
            listeners = tuple(self._listeners.values())
        for listener in listeners:
            listener(snapshot)
        return snapshot

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("backtesting controller is closed")
