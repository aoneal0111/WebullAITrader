from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from app.monte_carlo.models import (
    ExperimentMonteCarloResult, ExperimentSuiteMonteCarloResult, MonteCarloResult,
    WalkForwardMonteCarloResult,
)

WARNING = "COMPLETED PAPER-SIMULATION ROBUSTNESS ANALYSIS ONLY — NO OPTIMIZATION OR LIVE EXECUTION"


def monte_carlo_to_json(value: object) -> str:
    _validate(value)
    return json.dumps({"result": _safe(value), "schema_version": "1.0", "warning": WARNING},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def monte_carlo_to_text(value: object) -> str:
    _validate(value)
    lines = [WARNING, "Schema version: 1.0"]
    if isinstance(value, MonteCarloResult):
        lines.extend(_atomic(value))
    elif isinstance(value, ExperimentMonteCarloResult):
        lines.append(f"EXPERIMENT {value.experiment_id}")
        lines.extend(_atomic(value.result))
    elif isinstance(value, ExperimentSuiteMonteCarloResult):
        lines.extend(("EXPERIMENT SUITE", f"Dataset fingerprint: {value.dataset_fingerprint}"))
        for item in value.experiment_results:
            lines.append(f"EXPERIMENT {item.experiment_id}")
            lines.extend(_atomic(item.result))
    else:
        lines.extend(("WALK-FORWARD", f"Dataset fingerprint: {value.source_dataset_fingerprint}"))
        for item in value.window_results:
            lines.append(f"WINDOW {item.window_index} / EXPERIMENT {item.experiment_id}")
            lines.extend(_atomic(item.result))
    return "\n".join(lines) + "\n"


def _atomic(value: MonteCarloResult) -> list[str]:
    return [
        f"Source: {value.source_identity}", f"Sampling source: {value.source_kind}",
        f"Sampling mode: {value.config.sampling_mode.value}", f"Seed: {value.config.seed}",
        f"Simulations: {value.config.simulation_count}",
        f"Mean ending equity: {_show(value.ending_equity.mean)}",
        f"Median total return (decimal fraction): {_show(value.total_return.median)}",
        f"95th percentile maximum drawdown: {_show(value.maximum_drawdown.percentile_95)}",
        f"Probability finishing positive (percent): {_show(value.probabilities.finishing_positive)}",
        f"Probability exceeding original return (percent): {_show(value.probabilities.exceeding_original_return)}",
        f"Probability drawdown exceeds original (percent): {_show(value.probabilities.drawdown_exceeding_original)}",
        f"Probability profit factor above 1 (percent): {_show(value.probabilities.profit_factor_above_one)}",
        f"Probability expectancy positive (percent): {_show(value.probabilities.expectancy_positive)}",
        *[f"Warning: {item}" for item in value.warnings],
    ]


def _safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_safe(item) for item in value]
    return value


def _show(value: Decimal | None) -> str:
    return "N/A" if value is None else format(value, "f")


def _validate(value: object) -> None:
    if not isinstance(value, (MonteCarloResult, ExperimentMonteCarloResult,
                              ExperimentSuiteMonteCarloResult, WalkForwardMonteCarloResult)):
        raise ValueError("a completed Monte Carlo result is required")
