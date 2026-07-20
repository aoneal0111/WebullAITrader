from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.backtesting.models import HistoricalFrame
from app.experiments.models import ExperimentSuiteResult


class WalkForwardMode(StrEnum):
    ROLLING = "ROLLING"
    EXPANDING = "EXPANDING"
    FIXED_SIZE = "FIXED_SIZE"


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    mode: WalkForwardMode
    training_size: int
    evaluation_size: int
    step_size: int | None = None


@dataclass(frozen=True, slots=True)
class FrameRange:
    start_index: int
    end_index: int
    start_timestamp: datetime
    end_timestamp: datetime
    number_of_frames: int
    dataset_fingerprint: str


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    window_index: int
    training_range: FrameRange
    evaluation_range: FrameRange
    combined_frames: tuple[HistoricalFrame, ...]


@dataclass(frozen=True, slots=True)
class WalkForwardRun:
    window_index: int
    training_period: FrameRange
    evaluation_period: FrameRange
    experiment_results: ExperimentSuiteResult
    training_dataset_fingerprint: str
    evaluation_dataset_fingerprint: str
    combined_dataset_fingerprint: str
    configuration_fingerprints: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class WalkForwardAggregate:
    experiment_id: str
    number_of_windows: int
    aggregate_return: Decimal
    aggregate_drawdown: Decimal
    aggregate_win_rate: Decimal
    aggregate_profit_factor: Decimal | None
    aggregate_expectancy: Decimal | None
    aggregate_number_of_trades: int
    aggregate_rejected_proposals: int
    aggregate_gfv_rejections: int
    aggregate_compliance_rejections: int
    configuration_fingerprints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    mode: WalkForwardMode
    training_size: int
    evaluation_size: int
    step_size: int
    source_dataset_fingerprint: str
    runs: tuple[WalkForwardRun, ...]
    aggregates: tuple[WalkForwardAggregate, ...]
