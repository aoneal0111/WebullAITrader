from __future__ import annotations

from dataclasses import replace

from app.backtesting.datasource import frames_fingerprint
from app.backtesting.models import HistoricalFrame
from app.experiments.models import ExperimentDefinition
from app.experiments.runner import run_experiments
from app.walkforward.models import WalkForwardConfig, WalkForwardResult, WalkForwardRun
from app.walkforward.results import aggregate_walk_forward
from app.walkforward.splitter import effective_step, split_walk_forward


def run_walk_forward(
    frames: tuple[HistoricalFrame, ...], definitions: tuple[ExperimentDefinition, ...],
    config: WalkForwardConfig,
) -> WalkForwardResult:
    if not definitions:
        raise ValueError("at least one experiment definition is required")
    windows = split_walk_forward(frames, config)
    runs: list[WalkForwardRun] = []
    for window in windows:
        evaluation_timestamps = frozenset(
            frame.candle.close_timestamp
            for frame in window.combined_frames[-window.evaluation_range.number_of_frames:]
        )
        filtered = tuple(
            replace(
                definition,
                ai_responses=tuple(item for item in definition.ai_responses if item.candle_timestamp in evaluation_timestamps),
                order_intents=tuple(item for item in definition.order_intents if item.candle_timestamp in evaluation_timestamps),
                warmup_candles=max(definition.warmup_candles, window.training_range.number_of_frames),
            )
            for definition in definitions
        )
        suite = run_experiments(window.combined_frames, filtered)
        fingerprints = tuple(
            (item.experiment_id, item.configuration_fingerprint) for item in suite.experiment_results
        )
        runs.append(
            WalkForwardRun(
                window.window_index, window.training_range, window.evaluation_range, suite,
                window.training_range.dataset_fingerprint, window.evaluation_range.dataset_fingerprint,
                suite.dataset_fingerprint, fingerprints,
            )
        )
    run_tuple = tuple(runs)
    return WalkForwardResult(
        config.mode, config.training_size, config.evaluation_size, effective_step(config),
        frames_fingerprint(frames), run_tuple, aggregate_walk_forward(run_tuple),
    )


def run_walk_forward_experiment(
    frames: tuple[HistoricalFrame, ...], definition: ExperimentDefinition,
    config: WalkForwardConfig,
) -> WalkForwardResult:
    return run_walk_forward(frames, (definition,), config)
