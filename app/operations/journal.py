from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.operations.learning_runtime import runtime_inference_audit_payload
from app.operations.runtime import PaperRuntimeCycleResult


class AtomicPaperRuntimeJournal:
    """Persist completed paper-runtime cycles and latest session analytics atomically.

    The journal is a single canonical JSON document. Each successful call appends
    exactly one cycle and replaces the file atomically, so a partially written
    analytics snapshot is never visible.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def __call__(self, result: PaperRuntimeCycleResult) -> None:
        payload = self._load_or_create(result)
        cycles = payload["cycles"]
        if payload["session_id"] != result.session.session_id:
            raise ValueError("runtime journal session ID does not match cycle")
        expected_cycle = len(cycles) + 1
        if result.cycle != expected_cycle:
            raise ValueError(
                f"runtime journal expected cycle {expected_cycle}; "
                f"received {result.cycle}"
            )

        cycles.append(_cycle_payload(result))
        payload["latest_analytics"] = _analytics_payload(result)
        self._atomic_write(payload)

    def _load_or_create(self, result: PaperRuntimeCycleResult) -> dict[str, Any]:
        if not self._path.exists():
            return {
                "schema_version": "1",
                "session_id": result.session.session_id,
                "cycles": [],
                "latest_analytics": None,
            }
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("runtime journal is unreadable") from exc
        if not isinstance(payload, dict):
            raise ValueError("runtime journal root must be an object")
        if payload.get("schema_version") != "1":
            raise ValueError("unsupported runtime journal schema version")
        if not isinstance(payload.get("session_id"), str):
            raise ValueError("runtime journal session ID is invalid")
        cycles = payload.get("cycles")
        if not isinstance(cycles, list):
            raise ValueError("runtime journal cycles must be a list")
        if any(
            not isinstance(item, dict) or item.get("cycle") != index
            for index, item in enumerate(cycles, start=1)
        ):
            raise ValueError("runtime journal cycle sequence is invalid")
        return payload

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            text=True,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self._path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise


def _cycle_payload(result: PaperRuntimeCycleResult) -> dict[str, Any]:
    return {
        "cycle": result.cycle,
        "timestamp": result.timestamp.isoformat(),
        "symbols": list(result.symbols),
        "decisions": [_json_safe(decision) for decision in result.decisions],
        "inference_audits": [
            runtime_inference_audit_payload(audit)
            for audit in result.inference_audits
        ],
        "session_statistics": _json_safe(result.session.statistics),
    }


def _analytics_payload(result: PaperRuntimeCycleResult) -> dict[str, Any]:
    statistics = result.session.statistics
    return {
        "as_of_cycle": result.cycle,
        "as_of_timestamp": result.timestamp.isoformat(),
        "current_equity": format(statistics.current_equity, "f"),
        "peak_equity": format(statistics.peak_equity, "f"),
        "current_drawdown": format(statistics.current_drawdown, "f"),
        "realized_pnl": format(statistics.realized_pnl, "f"),
        "unrealized_pnl": format(statistics.unrealized_pnl, "f"),
        "decisions_processed": statistics.decisions_processed,
        "orders_attempted": statistics.orders_attempted,
        "orders_filled": statistics.orders_filled,
        "orders_rejected": statistics.orders_rejected,
        "orders_not_filled": statistics.orders_not_filled,
        "decisions_skipped": statistics.decisions_skipped,
    }


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json_safe(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, bool, float)):
        return value
    raise TypeError(f"unsupported runtime journal value: {type(value).__qualname__}")
