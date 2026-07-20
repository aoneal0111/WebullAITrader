from __future__ import annotations

from app.backtesting.datasource import frames_fingerprint, validate_frames
from app.backtesting.models import HistoricalFrame
from app.walkforward.models import FrameRange, WalkForwardConfig, WalkForwardMode, WalkForwardWindow


def split_walk_forward(
    frames: tuple[HistoricalFrame, ...], config: WalkForwardConfig
) -> tuple[WalkForwardWindow, ...]:
    validate_frames(frames)
    _validate_config(config)
    windows: list[WalkForwardWindow] = []
    train_size, test_size = config.training_size, config.evaluation_size
    if config.mode is WalkForwardMode.FIXED_SIZE:
        starts = range(0, len(frames), train_size + test_size)
        boundaries = ((start, start + train_size, start + train_size + test_size) for start in starts)
    elif config.mode is WalkForwardMode.ROLLING:
        step = config.step_size or 0
        boundaries = ((test_start - train_size, test_start, test_start + test_size)
                      for test_start in range(train_size, len(frames), step))
    else:
        step = config.step_size or 0
        boundaries = ((0, test_start, test_start + test_size)
                      for test_start in range(train_size, len(frames), step))
    for train_start, test_start, test_end in boundaries:
        if test_end > len(frames):
            continue
        training = frames[train_start:test_start]
        evaluation = frames[test_start:test_end]
        if len(training) < train_size or len(evaluation) < test_size:
            continue
        windows.append(
            WalkForwardWindow(
                len(windows), _frame_range(training, train_start, test_start),
                _frame_range(evaluation, test_start, test_end), (*training, *evaluation),
            )
        )
    if not windows:
        raise ValueError("source data does not contain one complete walk-forward window")
    return tuple(windows)


def effective_step(config: WalkForwardConfig) -> int:
    return config.training_size + config.evaluation_size if config.mode is WalkForwardMode.FIXED_SIZE else int(config.step_size or 0)


def _frame_range(frames: tuple[HistoricalFrame, ...], start: int, end: int) -> FrameRange:
    return FrameRange(start, end, frames[0].candle.open_timestamp, frames[-1].candle.close_timestamp,
                      len(frames), frames_fingerprint(frames))


def _validate_config(config: WalkForwardConfig) -> None:
    if not isinstance(config, WalkForwardConfig) or not isinstance(config.mode, WalkForwardMode):
        raise ValueError("walk-forward configuration is malformed")
    for name, value in (("training_size", config.training_size), ("evaluation_size", config.evaluation_size)):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if config.mode is WalkForwardMode.FIXED_SIZE:
        expected = config.training_size + config.evaluation_size
        if config.step_size not in (None, expected):
            raise ValueError("fixed-size step must equal training plus evaluation size")
    elif not isinstance(config.step_size, int) or isinstance(config.step_size, bool) or config.step_size <= 0:
        raise ValueError("rolling and expanding step_size must be a positive integer")
