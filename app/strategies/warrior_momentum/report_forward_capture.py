"""Read-only compatible-session cumulative reporting command."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from decimal import Decimal
import json
from pathlib import Path

from .forward_report import build_cumulative_reports
from .forward_store import ForwardCaptureStore


def _json(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "value"):
        return value.value
    raise TypeError(type(value).__name__)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path", type=Path,
        default=Path("data/warrior_momentum_v1_forward/forward_capture.sqlite3"),
    )
    arguments = parser.parse_args()
    reports = build_cumulative_reports(ForwardCaptureStore(arguments.path))
    print(json.dumps([asdict(item) for item in reports], default=_json, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
