from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from app.stress_testing.models import (
    ExperimentStressTestResult, ExperimentSuiteStressTestResult, StressTestResult,
    WalkForwardStressTestResult,
)

WARNING = "DETERMINISTIC HISTORICAL STRESS ANALYSIS ONLY — NO OPTIMIZATION OR LIVE EXECUTION"


def stress_test_to_json(value: object) -> str:
    _validate(value)
    return json.dumps({"result": _safe(value), "schema_version": "1.0", "warning": WARNING},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stress_test_to_text(value: object) -> str:
    _validate(value)
    lines = [WARNING, "Schema version: 1.0"]
    if isinstance(value, StressTestResult):
        lines.extend(_atomic(value))
    elif isinstance(value, ExperimentStressTestResult):
        lines.append(f"EXPERIMENT {value.experiment_id}")
        lines.extend(_atomic(value.result))
    elif isinstance(value, ExperimentSuiteStressTestResult):
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


def _atomic(value: StressTestResult) -> list[str]:
    lines = [f"Source: {value.source_identity}"]
    for item in value.scenarios:
        lines.extend((f"SCENARIO {item.scenario.value}", f"Available: {str(item.available).lower()}",
                      f"Observations: {item.observation_count}"))
        if item.metrics:
            lines.extend((f"Total return: {_show(item.metrics.total_return)}",
                          f"Maximum drawdown: {_show(item.metrics.maximum_drawdown)}",
                          f"Win rate (percent): {_show(item.metrics.win_rate)}",
                          f"Profit factor: {_show(item.metrics.profit_factor)}",
                          f"Expectancy: {_show(item.metrics.expectancy)}"))
        lines.extend(f"Warning: {warning}" for warning in item.warnings)
    return lines


def _safe(value: Any) -> Any:
    if isinstance(value, Decimal): return format(value, "f")
    if isinstance(value, datetime): return value.isoformat()
    if isinstance(value, Enum): return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (tuple, list)): return [_safe(item) for item in value]
    return value


def _show(value):
    return "N/A" if value is None else format(value, "f") if isinstance(value, Decimal) else str(value)


def _validate(value):
    if not isinstance(value, (StressTestResult, ExperimentStressTestResult,
                              ExperimentSuiteStressTestResult, WalkForwardStressTestResult)):
        raise ValueError("a completed stress-test result is required")
