"""One-way runtime adapter for bounded Entry Opportunity Value research.

The adapter accepts immutable facts from the existing Warrior decision boundary.
It owns no scanner, market-data, risk, order, broker, or account capability and
never returns a value that production control flow can consult.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from threading import RLock
from time import perf_counter

from app.market.calendar import EASTERN

from .models import EntryOpportunityValueInput, ShadowAction
from .service import EntryOpportunityValueService, ShadowServiceMetrics
from .store import JsonLinesObservationStore


_SIGNATURE_LIMIT = 10_000
_LATENCY_SAMPLES = 2_048
_ENTRY_VALIDITY = timedelta(minutes=1)
_DAY_END = time(20, 0)


@dataclass(frozen=True, slots=True)
class EntryOpportunityValueRuntimeMetrics:
    enabled: bool
    healthy: bool
    accepted: int
    completed: int
    suppressed: int
    rejected: int
    failed: int
    outstanding: int
    queue_depth: int
    queue_high_water: int
    maximum_worker_lag_ms: float
    producer_assembly_max_ms: float
    producer_assembly_average_ms: float
    queue_admission_max_ms: float
    episode_count: int
    classification_counts: tuple[tuple[str, int], ...]
    persistence_path: str
    accepting: bool
    stopped: bool
    last_error_type: str | None


class EntryOpportunityValueRuntimeObserver:
    """Translate existing decision facts into a failure-isolated EOV sidecar."""

    def __init__(
        self,
        *,
        enabled: bool,
        environment: str,
        path: str | Path,
        capacity: int = 1024,
        clock: Callable[[], datetime],
        research_context_source: Callable[
            [str, str, datetime], Mapping[str, object] | None
        ] | None = None,
        order_correlation_source: Callable[
            [str], Mapping[str, object] | None
        ] | None = None,
        service_factory: Callable[..., EntryOpportunityValueService] = (
            EntryOpportunityValueService
        ),
        store_factory: Callable[[Path], object] = JsonLinesObservationStore,
    ) -> None:
        normalized_environment = environment.strip().upper()
        self.enabled = bool(enabled) and normalized_environment in {
            "TEST", "PAPER", "SANDBOX",
        }
        self.environment = normalized_environment
        self.path = Path(path)
        self.capacity = capacity
        self._clock = clock
        self._research_context_source = research_context_source
        self._order_correlation_source = order_correlation_source
        self._service_factory = service_factory
        self._store_factory = store_factory
        self._service: EntryOpportunityValueService | None = None
        self._last_service_metrics: ShadowServiceMetrics | None = None
        self._signatures: OrderedDict[str, tuple[object, ...]] = (
            OrderedDict()
        )
        self._episodes: OrderedDict[str, None] = OrderedDict()
        self._assembly_samples: deque[float] = deque(maxlen=_LATENCY_SAMPLES)
        self._assembly_max_ms = 0.0
        self._admission_max_ms = 0.0
        self._suppressed = 0
        self._assembly_failures = 0
        self._last_error_type: str | None = None
        self._stopped = not self.enabled
        self._lock = RLock()

    def start(self, environment: str | None = None) -> None:
        """Start only the research worker; initialization failure is contained."""

        if not self.enabled:
            return
        with self._lock:
            if self._service is not None:
                return
            try:
                if self.capacity <= 0:
                    raise ValueError("EOV queue capacity must be positive")
                if self.path.suffix.lower() != ".jsonl":
                    raise ValueError("EOV persistence path must be a JSONL file")
                if self.path.exists() and not self.path.is_file():
                    raise IsADirectoryError(str(self.path))
                store = self._store_factory(self.path)
                self._service = self._service_factory(
                    store, capacity=self.capacity, clock=self._clock,
                )
                self._stopped = False
                self._last_error_type = None
            except Exception as exc:
                self._assembly_failures += 1
                self._last_error_type = type(exc).__name__
                self._stopped = True

    def observe_decision(
        self,
        *,
        value: object,
        candidate: object,
        signal: object | None,
        planned_quantity: int | None,
        decision_state: str,
        lifecycle_id: str,
    ) -> None:
        """Observe one decision without exposing success or failure to execution."""

        started = perf_counter()
        try:
            with self._lock:
                service = self._service
            if not self.enabled or service is None:
                return
            if signal is None or planned_quantity is None or planned_quantity <= 0:
                return
            context = self._build_context(
                value=value,
                candidate=candidate,
                signal=signal,
                planned_quantity=planned_quantity,
                decision_state=decision_state,
                lifecycle_id=lifecycle_id,
            )
            signature = self._signature(context, decision_state)
            key = context.lifecycle_id
            with self._lock:
                if self._signatures.get(key) == signature:
                    self._suppressed += 1
                    return
            admission_started = perf_counter()
            admitted = service.observe(context)
            admission_ms = (perf_counter() - admission_started) * 1000.0
            with self._lock:
                self._admission_max_ms = max(
                    self._admission_max_ms, admission_ms,
                )
                if admitted:
                    self._remember(self._signatures, key, signature)
                    self._remember(self._episodes, context.lifecycle_id, None)
        except Exception as exc:
            with self._lock:
                self._assembly_failures += 1
                self._last_error_type = type(exc).__name__
        finally:
            elapsed = (perf_counter() - started) * 1000.0
            with self._lock:
                self._assembly_samples.append(elapsed)
                self._assembly_max_ms = max(self._assembly_max_ms, elapsed)

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        with self._lock:
            service = self._service
        if service is None:
            with self._lock:
                self._stopped = True
            return True
        try:
            stopped = service.close(timeout_seconds=timeout_seconds)
            snapshot = service.metrics()
        except Exception as exc:
            stopped = False
            snapshot = None
            with self._lock:
                self._assembly_failures += 1
                self._last_error_type = type(exc).__name__
        with self._lock:
            if snapshot is not None:
                self._last_service_metrics = snapshot
            if stopped and self._service is service:
                self._service = None
            self._stopped = stopped
        return stopped

    def metrics(self) -> EntryOpportunityValueRuntimeMetrics:
        with self._lock:
            service = self._service
            snapshot = (
                self._last_service_metrics
                if service is None else service.metrics()
            )
            samples = tuple(self._assembly_samples)
            failed = self._assembly_failures + (
                0 if snapshot is None else snapshot.failures
            )
            return EntryOpportunityValueRuntimeMetrics(
                enabled=self.enabled,
                healthy=(not self.enabled or failed == 0),
                accepted=0 if snapshot is None else snapshot.observations_accepted,
                completed=0 if snapshot is None else snapshot.observations_completed,
                suppressed=self._suppressed,
                rejected=0 if snapshot is None else snapshot.rejections,
                failed=failed,
                outstanding=0 if snapshot is None else snapshot.outstanding,
                queue_depth=0 if snapshot is None else snapshot.queue_depth,
                queue_high_water=0 if snapshot is None else snapshot.queue_high_water,
                maximum_worker_lag_ms=(
                    0.0 if snapshot is None else snapshot.maximum_worker_lag_ms
                ),
                producer_assembly_max_ms=self._assembly_max_ms,
                producer_assembly_average_ms=(
                    0.0 if not samples else sum(samples) / len(samples)
                ),
                queue_admission_max_ms=self._admission_max_ms,
                episode_count=len(self._episodes),
                classification_counts=tuple(
                    (action.value, count)
                    for action, count in (
                        () if snapshot is None else snapshot.classification_counts
                    )
                ),
                persistence_path=str(self.path),
                accepting=False if snapshot is None else snapshot.accepting,
                stopped=self._stopped,
                last_error_type=self._last_error_type,
            )

    def _build_context(
        self, *, value: object, candidate: object, signal: object,
        planned_quantity: int, decision_state: str, lifecycle_id: str,
    ) -> EntryOpportunityValueInput:
        observation = getattr(value, "observation")
        cutoff = (
            getattr(value, "evaluation_timestamp", None)
            or getattr(candidate, "timestamp")
        )
        _require_aware(cutoff, "decision cutoff")
        entry_at = getattr(signal, "timestamp")
        _require_aware(entry_at, "entry plan timestamp")
        research = self._cutoff_research_context(
            str(getattr(candidate, "symbol")), lifecycle_id, cutoff,
        )
        correlation = self._order_context(lifecycle_id)
        quote_timestamp = _past_time(
            getattr(value, "quote_observed_at", None), cutoff,
        )
        quote_received = _past_time(
            getattr(observation, "quote_received_timestamp", None), cutoff,
        )
        eastern = cutoff.astimezone(EASTERN)
        return EntryOpportunityValueInput(
            symbol=str(getattr(candidate, "symbol")),
            decision_cutoff=cutoff,
            environment=self.environment,
            session=str(getattr(candidate, "session")),
            strategy=str(getattr(signal, "strategy_id")),
            setup=_enum_value(getattr(signal, "setup_type")),
            lifecycle_id=lifecycle_id,
            opportunity_id=_optional_text(research.get("opportunity_id")),
            entry_plan_at=entry_at,
            entry_ready_at=entry_at,
            planned_entry_price=Decimal(getattr(signal, "entry_trigger")),
            structural_stop=Decimal(getattr(signal, "stop_price")),
            planned_quantity=planned_quantity,
            bid=_decimal_or_none(getattr(observation, "bid", None)),
            ask=_decimal_or_none(getattr(observation, "ask", None)),
            last=_decimal_or_none(getattr(observation, "price", None)),
            quote_timestamp=quote_timestamp,
            quote_received_at=quote_received,
            best_bid_size=_decimal_or_none(
                getattr(value, "best_bid_size", None),
            ),
            best_ask_size=_decimal_or_none(
                getattr(value, "best_ask_size", None),
            ),
            scanner_rank=getattr(value, "scanner_rank", None),
            scanner_score=_decimal_or_none(
                getattr(value, "scanner_score", None)
                or getattr(getattr(candidate, "score", None), "total", None)
            ),
            percentage_change=_decimal_or_none(
                getattr(candidate, "percentage_change", None),
            ),
            relative_volume=_decimal_or_none(
                getattr(candidate, "relative_volume", None),
            ),
            detector_memberships=tuple(
                str(item) for item in research.get("detector_memberships", ())
            ),
            technical_state=_enum_value(getattr(candidate, "status", "UNKNOWN")),
            entry_ready_state=decision_state,
            quote_provenance=str(
                getattr(value, "quote_provenance", "SHARED_WARRIOR_POINT_IN_TIME")
            ),
            source_event_identity=(
                f"warrior:{getattr(candidate, 'symbol')}:{cutoff.isoformat()}"
            ),
            order_id=_optional_text(correlation.get("order_id")),
            client_order_id=_optional_text(correlation.get("client_order_id")),
            trade_intelligence_experience_id=_optional_text(
                research.get("trade_intelligence_experience_id")
            ),
            technical_confidence=_decimal_or_none(
                getattr(getattr(candidate, "score", None), "total", None),
            ),
            valid_until=entry_at + _ENTRY_VALIDITY,
            day_boundary=datetime.combine(
                eastern.date(), _DAY_END, tzinfo=EASTERN,
            ),
        )

    def _cutoff_research_context(
        self, symbol: str, lifecycle_id: str, cutoff: datetime,
    ) -> dict[str, object]:
        source = self._research_context_source
        if source is None:
            return {}
        value = source(symbol.strip().upper(), lifecycle_id, cutoff)
        if value is None:
            return {}
        result = dict(value)
        observed_at = result.get("observed_at")
        if observed_at is not None:
            if not isinstance(observed_at, datetime):
                return {}
            _require_aware(observed_at, "research context timestamp")
            if observed_at > cutoff:
                return {}
        return result

    def _order_context(self, lifecycle_id: str) -> dict[str, object]:
        source = self._order_correlation_source
        if source is None:
            return {}
        value = source(lifecycle_id)
        return {} if value is None else dict(value)

    @staticmethod
    def _signature(
        context: EntryOpportunityValueInput, decision_state: str,
    ) -> tuple[object, ...]:
        return (
            decision_state,
            context.planned_quantity,
            context.planned_entry_price,
            context.structural_stop,
            context.bid,
            context.ask,
            context.last,
            context.quote_timestamp,
            context.scanner_score,
            context.detector_memberships,
            context.order_id,
            context.client_order_id,
        )

    @staticmethod
    def _remember(values: OrderedDict, key: object, value: object) -> None:
        values[key] = value
        values.move_to_end(key)
        while len(values) > _SIGNATURE_LIMIT:
            values.popitem(last=False)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _past_time(value: object, cutoff: datetime) -> datetime | None:
    if value is None or not isinstance(value, datetime):
        return None
    _require_aware(value, "point-in-time timestamp")
    if value > cutoff:
        raise ValueError("point-in-time timestamp cannot exceed decision cutoff")
    return value


def _decimal_or_none(value: object) -> Decimal | None:
    return None if value is None else Decimal(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


__all__ = [
    "EntryOpportunityValueRuntimeMetrics",
    "EntryOpportunityValueRuntimeObserver",
]
