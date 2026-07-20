from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from app.experiments.models import ExperimentSuiteResult


def comparison_to_json(result: ExperimentSuiteResult) -> str:
    payload = {
        "dataset_fingerprint": result.dataset_fingerprint,
        "experiments": [_safe(asdict(row)) for row in result.comparison_rows],
        "notes": {item.experiment_id: item.notes for item in result.experiment_results},
        "warning": "HISTORICAL PAPER SIMULATION ONLY; NO WINNER IS SELECTED",
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def comparison_to_text(result: ExperimentSuiteResult) -> str:
    lines = [
        "HISTORICAL PAPER SIMULATION ONLY — NO WINNER IS SELECTED",
        "experiment | return | drawdown | win rate | profit factor | expectancy | trades | rejected | GFV rejected | compliance rejected",
    ]
    for row in result.comparison_rows:
        lines.append(
            " | ".join((
                row.experiment_id, f"{row.total_return}%", f"{row.maximum_drawdown}%",
                f"{row.win_rate}%", _display(row.profit_factor), _display(row.expectancy),
                str(row.number_of_trades), str(row.number_of_rejected_proposals),
                str(row.number_of_gfv_rejections), str(row.number_of_compliance_rejections),
            ))
        )
    return "\n".join(lines)


def _safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe(item) for item in value]
    return value


def _display(value: Decimal | None) -> str:
    return "N/A" if value is None else format(value, "f")
