from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.walkforward.models import WalkForwardResult


def walk_forward_to_json(result: WalkForwardResult) -> str:
    payload = {
        "mode": result.mode.value,
        "training_size": result.training_size,
        "evaluation_size": result.evaluation_size,
        "step_size": result.step_size,
        "source_dataset_fingerprint": result.source_dataset_fingerprint,
        "windows": [
            {
                "window_index": run.window_index,
                "training_period": _safe(asdict(run.training_period)),
                "evaluation_period": _safe(asdict(run.evaluation_period)),
                "training_dataset_fingerprint": run.training_dataset_fingerprint,
                "evaluation_dataset_fingerprint": run.evaluation_dataset_fingerprint,
                "combined_dataset_fingerprint": run.combined_dataset_fingerprint,
                "configuration_fingerprints": _safe(run.configuration_fingerprints),
                "comparisons": _safe(tuple(asdict(row) for row in run.experiment_results.comparison_rows)),
            }
            for run in result.runs
        ],
        "aggregates": _safe(tuple(asdict(item) for item in result.aggregates)),
        "warning": "HISTORICAL WALK-FORWARD PAPER VALIDATION ONLY; NO OPTIMIZATION OR WINNER SELECTION",
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def walk_forward_to_text(result: WalkForwardResult) -> str:
    lines = [
        "HISTORICAL WALK-FORWARD PAPER VALIDATION ONLY — NO OPTIMIZATION OR WINNER SELECTION",
        f"mode={result.mode.value} train={result.training_size} test={result.evaluation_size} step={result.step_size}",
    ]
    for run in result.runs:
        lines.append(
            f"window {run.window_index}: train {run.training_period.start_timestamp.isoformat()}..{run.training_period.end_timestamp.isoformat()} "
            f"test {run.evaluation_period.start_timestamp.isoformat()}..{run.evaluation_period.end_timestamp.isoformat()}"
        )
        for row in run.experiment_results.comparison_rows:
            lines.append(
                f"  {row.experiment_id}: return={row.total_return}% drawdown={row.maximum_drawdown}% "
                f"win_rate={row.win_rate}% trades={row.number_of_trades} rejected={row.number_of_rejected_proposals}"
            )
    lines.append("aggregates:")
    for item in result.aggregates:
        lines.append(
            f"  {item.experiment_id}: return={item.aggregate_return}% drawdown={item.aggregate_drawdown}% "
            f"win_rate={item.aggregate_win_rate}% profit_factor={_display(item.aggregate_profit_factor)} "
            f"expectancy={_display(item.aggregate_expectancy)} trades={item.aggregate_number_of_trades} "
            f"rejected={item.aggregate_rejected_proposals} GFV={item.aggregate_gfv_rejections} "
            f"compliance={item.aggregate_compliance_rejections}"
        )
    return "\n".join(lines)


def _safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe(item) for item in value]
    return value


def _display(value: Decimal | None) -> str:
    return "N/A" if value is None else format(value, "f")
