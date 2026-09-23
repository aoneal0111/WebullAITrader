"""Bounded, non-authoritative PAPER runtime validation capture."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
import json
import os
from pathlib import Path
from queue import Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Callable
from decimal import Decimal

from app.performance_diagnostics import performance_diagnostics


DEFAULT_CAPTURE_DIR = Path(r"C:\Users\aonea\AtlasForensics\paper-runtime-validation")
DEFAULT_INTERVAL_SECONDS = 1.0
MAX_CANDIDATE_SAMPLES = 250
MAX_TRANSITIONS = 250
MAX_QUEUE_RECORDS = 512


class PaperValidationCapture:
    """Best-effort JSONL capture isolated from trading authority."""

    def __init__(
        self,
        snapshot_provider: Callable[[], dict[str, object]],
        *,
        output_dir: str | Path = DEFAULT_CAPTURE_DIR,
        enabled: bool = False,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], datetime] | None = None,
        max_candidate_samples: int = MAX_CANDIDATE_SAMPLES,
        max_transitions: int = MAX_TRANSITIONS,
    ) -> None:
        if not callable(snapshot_provider):
            raise TypeError("snapshot provider must be callable")
        if interval_seconds <= 0:
            raise ValueError("capture interval must be positive")
        if max_candidate_samples <= 0 or max_transitions <= 0:
            raise ValueError("capture bounds must be positive")
        self._provider = snapshot_provider
        self._output_dir = Path(output_dir)
        self._enabled = bool(enabled)
        self._interval_seconds = float(interval_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_candidate_samples = int(max_candidate_samples)
        self._max_transitions = int(max_transitions)
        self._queue: Queue[dict[str, object] | None] = Queue(maxsize=MAX_QUEUE_RECORDS)
        self._stop = Event()
        self._lock = Lock()
        self._writer_thread: Thread | None = None
        self._sampler_thread: Thread | None = None
        self._path: Path | None = None
        self._started_at: datetime | None = None
        self._last_snapshot: dict[str, object] | None = None
        self._candidate_samples: deque[dict[str, object]] = deque(
            maxlen=self._max_candidate_samples
        )
        self._transitions: deque[dict[str, object]] = deque(
            maxlen=self._max_transitions
        )
        self._funnel_totals: Counter[str] = Counter()
        self._reason_totals: Counter[str] = Counter()
        self._dropped_records = 0
        self._writer_failures = 0
        self._closed = False

    @classmethod
    def from_environment(
        cls,
        snapshot_provider: Callable[[], dict[str, object]],
    ) -> "PaperValidationCapture":
        enabled = os.environ.get("ATLAS_PAPER_VALIDATION_CAPTURE", "").strip().lower()
        output_dir = os.environ.get(
            "ATLAS_PAPER_VALIDATION_CAPTURE_DIR", str(DEFAULT_CAPTURE_DIR)
        )
        interval = os.environ.get(
            "ATLAS_PAPER_VALIDATION_CAPTURE_INTERVAL_SECONDS", "1.0"
        )
        try:
            interval_seconds = float(interval)
        except (TypeError, ValueError):
            interval_seconds = DEFAULT_INTERVAL_SECONDS
        return cls(
            snapshot_provider,
            output_dir=output_dir,
            enabled=enabled in {"1", "true", "yes", "on"},
            interval_seconds=interval_seconds,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled and self._path is not None and not self._closed

    @property
    def path(self) -> Path | None:
        return self._path

    def start(self) -> None:
        if not self._enabled or self._closed:
            return
        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            started = self._clock()
            self._started_at = started
            stem = f"atlas-paper-runtime-validation-{started.strftime('%Y%m%d-%H%M%S')}"
            self._path = self._next_session_path(stem)
            self._path.open("x", encoding="utf-8").close()
            self._write_pointer(self._path)
            self._writer_thread = Thread(
                target=self._writer_loop,
                name="atlas-paper-validation-writer",
                daemon=True,
            )
            self._writer_thread.start()
            self._sampler_thread = Thread(
                target=self._sample_loop,
                name="atlas-paper-validation-sampler",
                daemon=True,
            )
            self._sampler_thread.start()
            self._enqueue({
                "record_type": "session_start",
                "timestamp": started.isoformat(),
            })
        except Exception:
            self._disable()

    def sample(self) -> None:
        if not self.enabled:
            return
        try:
            snapshot = _sanitize(self._provider())
            if not isinstance(snapshot, dict):
                snapshot = {"provider": snapshot}
            timestamp = self._clock().isoformat()
            record = {
                "record_type": "periodic_snapshot",
                "timestamp": timestamp,
                "snapshot": snapshot,
            }
            self._collect_summary(snapshot)
            self._detect_transitions(snapshot, timestamp)
            self._enqueue(record)
        except Exception:
            self._increment_drop()

    def record_transition(
        self,
        category: str,
        previous_state: object,
        new_state: object,
        *,
        reason_category: str | None = None,
        exception_class: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        try:
            timestamp = self._clock().isoformat()
            transition = {
                "record_type": "transition",
                "timestamp": timestamp,
                "event_category": _bounded_text(category),
                "previous_state": _bounded_text(previous_state),
                "new_state": _bounded_text(new_state),
                "reason_category": _bounded_text(reason_category),
                "exception_class": _bounded_exception_class(exception_class),
            }
            with self._lock:
                if len(self._transitions) >= self._max_transitions:
                    self._increment_drop_locked()
                else:
                    self._transitions.append(transition)
            self._enqueue(transition)
        except Exception:
            self._increment_drop()

    def close(self) -> None:
        if not self._enabled or self._closed:
            return
        try:
            # Stop sampling before taking the final snapshot so shutdown cannot
            # race the summary or enqueue work after the sentinel.
            self._stop.set()
            if self._sampler_thread is not None:
                self._sampler_thread.join(2.0)
            self.sample()
            ended = self._clock()
            summary = {
                "record_type": "session_summary",
                "timestamp": ended.isoformat(),
                "session_start": (
                    None if self._started_at is None else self._started_at.isoformat()
                ),
                "session_end": ended.isoformat(),
                "duration_seconds": (
                    None
                    if self._started_at is None
                    else max(0.0, (ended - self._started_at).total_seconds())
                ),
                "funnel_totals": dict(self._funnel_totals),
                "reason_code_totals": dict(self._reason_totals),
                "candidate_samples": list(self._candidate_samples),
                "transition_count": len(self._transitions),
                "diagnostic_dropped_records": self._dropped_records,
                "writer_failures": self._writer_failures,
            }
            self._enqueue(summary)
            self._queue.join()
            self._queue.put(None)
            if self._writer_thread is not None:
                self._writer_thread.join(2.0)
        except Exception:
            self._disable()
        finally:
            self._closed = True

    def _sample_loop(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self.sample()

    def _writer_loop(self) -> None:
        while True:
            record = self._queue.get()
            try:
                if record is None:
                    return
                path = self._path
                if path is None:
                    return
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
                    handle.flush()
            except Exception:
                self._writer_failures += 1
                self._disable()
            finally:
                self._queue.task_done()

    def _enqueue(self, record: dict[str, object]) -> None:
        try:
            self._queue.put_nowait(record)
        except Full:
            self._increment_drop()

    def _collect_summary(self, snapshot: dict[str, object]) -> None:
        funnel = snapshot.get("funnel_totals")
        if isinstance(funnel, dict):
            self._funnel_totals.update({str(k): int(v) for k, v in funnel.items() if _is_int(v)})
        reasons = snapshot.get("reason_code_totals")
        if isinstance(reasons, dict):
            self._reason_totals.update({str(k): int(v) for k, v in reasons.items() if _is_int(v)})
        candidates = snapshot.get("candidate_samples")
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict) and len(self._candidate_samples) < self._max_candidate_samples:
                    self._candidate_samples.append(candidate)

    def _detect_transitions(self, snapshot: dict[str, object], timestamp: str) -> None:
        previous = self._last_snapshot
        self._last_snapshot = snapshot
        if previous is None:
            return
        for category, path in (
            ("ATLAS_RUNTIME", ("runtime", "health")),
            ("SCANNER", ("scanner", "health")),
            ("WARRIOR", ("warrior", "health")),
            ("RAW_INGRESS", ("raw_ingress", "overflow_count")),
            ("AUTHORITATIVE_LANE", ("authoritative_lane", "failure_state")),
            ("MARKET_DATA_CONSUMER", ("market_data_consumer", "consumer_state")),
        ):
            old = _nested(previous, path)
            new = _nested(snapshot, path)
            if old != new and (old is not None or new is not None):
                self.record_transition(category, old, new)

    def _write_pointer(self, path: Path) -> None:
        pointer = self._output_dir / "LATEST.txt"
        pointer.write_text(str(path.resolve()) + "\n", encoding="utf-8")

    def _next_session_path(self, stem: str) -> Path:
        candidate = self._output_dir / f"{stem}.jsonl"
        suffix = 1
        while candidate.exists():
            candidate = self._output_dir / f"{stem}-{suffix}.jsonl"
            suffix += 1
        return candidate

    def _increment_drop(self) -> None:
        with self._lock:
            self._increment_drop_locked()

    def _increment_drop_locked(self) -> None:
        self._dropped_records += 1

    def _disable(self) -> None:
        self._enabled = False


def composition_snapshot(composition: object) -> dict[str, object]:
    """Build a sanitized read-only snapshot from the desktop composition."""
    state_store = getattr(composition, "state_store", None)
    state = state_store.snapshot() if state_store is not None else None
    runtime_service = getattr(composition, "runtime_service", None)
    with _service_driver(runtime_service) as driver:
        driver_metrics = _metrics(driver)
        consumer_metrics = _call(driver, "market_data_consumer_metrics")
    warrior = getattr(composition, "warrior_forward_sidecar", None)
    warrior_snapshot = _call(warrior, "snapshot")
    warrior_projection_metrics = _call(warrior, "projection_metrics")
    scanner = getattr(driver, "_scanner", None)
    scanner_metrics = _metrics(scanner)
    transport = getattr(driver, "_market_data", None)
    raw_metrics = _metrics(transport)
    if not raw_metrics:
        raw_metrics = _nested(driver_metrics, ("market_data",)) or {}
    lane_metrics = _find_metric_group(driver_metrics, "authoritative_lane")
    mailbox_metrics = _find_metric_group(driver_metrics, "evaluation_mailbox")
    health = None if state is None else getattr(state, "health_projection", None)
    runtime = None if state is None else getattr(state, "runtime", None)
    positions = () if state is None else getattr(state, "paper_positions", ())
    orders = () if state is None else getattr(state, "paper_orders", ())
    warrior_data = _warrior_snapshot(warrior_snapshot)
    warrior_data["projection_metrics"] = _sanitize(warrior_projection_metrics or {})
    warrior_data["entry_funnel"] = _sanitize(
        performance_diagnostics.entry_conversion_metrics()
    )
    return {
        "session": {
            "environment": "PAPER",
            "live_trading_enabled": False,
            "warrior_forward_paper_enabled": bool(warrior is not None),
            "market_session": _attr(health, "market_session_status"),
        },
        "runtime": {
            "health": _attr(runtime, "phase"),
            "scanner_health": _attr(health, "scanner_status"),
            "market_data_health": _attr(health, "market_data_status"),
            "cycles": _attr(runtime, "cycles_completed"),
        },
        "raw_ingress": raw_metrics,
        "market_data_consumer": _sanitize(consumer_metrics),
        "authoritative_lane": lane_metrics,
        "evaluation_mailbox": mailbox_metrics,
        "warrior": warrior_data,
        "funnel_totals": warrior_data.get("funnel_totals", {}),
        "reason_code_totals": warrior_data.get("reason_code_totals", {}),
        "scanner": scanner_metrics,
        "paper_account": {
            "position_count": len(positions),
            "working_order_count": len(orders),
            "positions": _safe_records(positions),
            "working_orders": _safe_records(orders),
        },
        "performance": _sanitize(asdict(performance_diagnostics.snapshot())),
    }


def _warrior_snapshot(snapshot: object) -> dict[str, object]:
    if snapshot is None:
        return {}
    value = _sanitize(snapshot)
    if not isinstance(value, dict):
        return {"snapshot": value}
    summary = value.get("summary", {})
    candidates = value.get("items", [])[:MAX_CANDIDATE_SAMPLES]
    funnel = {}
    if isinstance(summary, dict):
        for source, target in (
            ("discovered", "scanner_observed"),
            ("qualified", "scanner_qualified"),
            ("setup_forming", "setup_forming"),
            ("triggered", "setup_triggered"),
            ("entry_ready", "entry_signal"),
        ):
            if _is_int(summary.get(source)):
                funnel[target] = summary[source]
    reasons: Counter[str] = Counter()
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, dict):
                for reason in candidate.get("blocking_reasons", ()):
                    reasons[str(reason)] += 1
    return {
        "health": value.get("health"),
        "observability_health": value.get("observability_health"),
        "entry_authorized": value.get("entry_authorized"),
        "last_observation_at": value.get("last_observation_at"),
        "last_full_evaluation_at": value.get("last_full_evaluation_at"),
        "last_health_transition": value.get("last_health_transition"),
        "summary": summary,
        "funnel_totals": funnel,
        "reason_code_totals": dict(reasons),
        "metrics": value.get("metrics", {}),
        "candidate_samples": candidates,
    }


class _service_driver:
    def __init__(self, service: object | None):
        self.service = service
        self.driver = None

    def __enter__(self):
        if self.service is not None:
            lock = getattr(self.service, "_lock", None)
            if lock is not None:
                with lock:
                    self.driver = getattr(self.service, "_driver", None)
        return self.driver

    def __exit__(self, *_args):
        return False


def _metrics(value: object) -> dict[str, object]:
    provider = getattr(value, "memory_metrics", None)
    if not callable(provider):
        return {}
    try:
        return _sanitize(provider())
    except Exception:
        return {}


def _call(value: object, method: str) -> object:
    function = getattr(value, method, None)
    if not callable(function):
        return None
    try:
        return function()
    except Exception:
        return None


def _find_metric_group(metrics: dict[str, object], needle: str) -> dict[str, object]:
    found = {}
    for key, value in metrics.items():
        if needle in str(key):
            found[key] = value
    return found


def _nested(value: object, path: tuple[str, ...]) -> object:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _attr(value: object, name: str) -> object:
    return None if value is None else getattr(value, name, None)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _bounded_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)[:160]


def _bounded_exception_class(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).split(":", 1)[0].split(".")[-1]
    return text[:120]


def _sanitize(value: object, *, depth: int = 0) -> object:
    if depth > 5:
        return "<depth-limited>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _sanitize(asdict(value), depth=depth + 1)
    if isinstance(value, dict):
        return {
            str(key)[:120]: _sanitize(item, depth=depth + 1)
            for key, item in list(value.items())[:500]
            if not _sensitive_key(key)
        }
    if isinstance(value, (tuple, list, deque)):
        return [_sanitize(item, depth=depth + 1) for item in list(value)[:250]]
    return "<unsupported>"


def _sensitive_key(value: object) -> bool:
    key = str(value).lower().replace("-", "_")
    return any(
        token in key
        for token in (
            "account",
            "credential",
            "token",
            "secret",
            "password",
            "authorization",
            "header",
            "query",
            "request_body",
            "response_body",
        )
    )


def _safe_records(values: object) -> list[dict[str, object]]:
    if not isinstance(values, (tuple, list)):
        return []
    records: list[dict[str, object]] = []
    for value in list(values)[:250]:
        sanitized = _sanitize(value)
        if isinstance(sanitized, dict):
            records.append(sanitized)
    return records


__all__ = [
    "DEFAULT_CAPTURE_DIR",
    "PaperValidationCapture",
    "composition_snapshot",
]
