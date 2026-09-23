"""Desktop-owned, stream-sharing Warrior V1 forward-paper observer."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import logging
from pathlib import Path
import re
from threading import RLock
from time import monotonic, perf_counter
from typing import Callable, Iterable

from app.live_scanner.session import scanner_session
from app.market.calendar import EASTERN
from app.market_data.models import (
    MarketEvent, MarketEventType, QuotePayload, TradePayload, VolumeSemantics,
)
from app.scanner_adapter.adapter import MarketEventScannerAdapter
from app.performance_diagnostics import performance_diagnostics
from app.services.runtime_diagnostics import log_runtime_exception

from .configuration import WarriorMomentumConfig
from .features import canonical_completed_history, contiguous_tail, current_completed_bar_tail
from .forward_models import (
    CAPTURE_SCHEMA_VERSION, CaptureMetrics, CaptureRecord, CaptureRecordType,
    FloatProvenance, ForwardCaptureConfiguration, PaperAccountContext,
    PointInTimeObservation, canonical_json, records_with_configuration_fingerprint,
)
from .forward_queue import ForwardCaptureWriter
from .forward_report import DailyForwardReport
from .report_worker import ReportWorkerMetrics, WarriorReportWorker
from .forward_runtime import WarriorForwardCaptureService
from .execution_quote import ExecutionQuoteSource
from .forward_store import ForwardCaptureStore
from .order_flow_runtime import (
    OrderFlowPollingService, OrderFlowPriority,
)
from .projection_handoff import BoundedProjectionHandoff
from .models import CandidateStatus, MinuteBar, MomentumCandidate, SetupState
from .observability import NoOpWarriorObservabilitySink
from .runtime import WarriorMomentumRuntime
from .shadow_latched import (
    ShadowLatchedTransition,
    ShadowMarketObservation,
)
from .session_risk import entry_cutoff_reached, flatten_window_reached


_RUNTIME_LOGGER = logging.getLogger("atlas.runtime")

STRATEGY_VERSION = "WARRIOR_MOMENTUM_V1"
_PROTECTION_AUDIT_INTERVAL_SECONDS = 15.0
# Triggered structures need fresh execution-sensitive decisions without turning
# every market tick into a full Warrior/research pass. This cadence is a
# processing bound, not a trading threshold or setup-policy change.
_INTRAMINUTE_REEVALUATION_SECONDS = 1.0
# A non-triggered verdict (especially NO_SETUP) is still time-sensitive.  Keep
# it fresh for active scanner candidates without letting every hot-feed tick
# become a full research/evaluation pass.  This matches the entry-data stale
# boundary, so a prior veto cannot remain authoritative after its inputs age.
_ACTIVE_CANDIDATE_MIN_REEVALUATION_SECONDS = 5.0
_ACTIVE_CANDIDATE_MAX_REEVALUATION_SECONDS = 30.0
_ACTIVE_CANDIDATE_PRICE_CHANGE_PERCENT = Decimal("1.0")
_ACTIVE_CANDIDATE_VOLUME_CHANGE_PERCENT = Decimal("10")
_ACTIVE_CANDIDATE_MIN_VOLUME_CHANGE = Decimal("250000")


def _meaningful_active_candidate_change(
    prior: MomentumCandidate,
    observation: object,
) -> bool:
    """Bound quote-led refreshes to material changes, not feed frequency."""

    price = getattr(observation, "price", None)
    price_changed = bool(
        price is not None
        and prior.price > 0
        and abs(price - prior.price) / prior.price * Decimal("100")
        >= _ACTIVE_CANDIDATE_PRICE_CHANGE_PERCENT
    )
    volume = getattr(observation, "current_volume", None)
    volume_delta = (
        Decimal("0")
        if volume is None
        else max(Decimal("0"), volume - prior.volume)
    )
    volume_changed = bool(
        prior.volume > 0
        and volume_delta >= _ACTIVE_CANDIDATE_MIN_VOLUME_CHANGE
        and volume_delta / prior.volume * Decimal("100")
        >= _ACTIVE_CANDIDATE_VOLUME_CHANGE_PERCENT
    )
    return price_changed or volume_changed


def _safe_warrior_observe(sink: object | None, event: str, symbol: object, **fields: object) -> None:
    """Best-effort diagnostic callback which cannot affect Warrior processing."""
    try:
        callback = getattr(sink, "emit_warrior", None)
        if callable(callback):
            callback(event=event, symbol=str(symbol).strip().upper(), **fields)
    except Exception:
        return None


class WarriorCaptureHealth(StrEnum):
    DISABLED = "DISABLED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPED = "STOPPED"


class WarriorObservabilityHealth(StrEnum):
    DISABLED = "DISABLED"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True, slots=True)
class WarriorHealthTransition:
    timestamp: datetime
    previous_state: str
    new_state: str
    category: str
    reason: str
    exception_class: str | None = None
    diagnostic_dropped_records: int = 0
    critical_failure_state: bool = False
    critical_failure_count: int = 0
    capture_queue_depth: int = 0
    diagnostic_queue_depth: int = 0
    last_observation_at: datetime | None = None
    last_full_evaluation_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WarriorPaperSummary:
    discovered: int = 0
    stocks_in_play: int = 0
    near: int = 0
    qualified: int = 0
    setup_forming: int = 0
    triggered: int = 0
    entry_ready: int = 0
    open_paper_trades: int = 0
    today_paper_r: Decimal | None = None
    today_trades: int = 0
    triggered_but_blocked: int = 0
    tracked_counterfactuals: int = 0


@dataclass(frozen=True, slots=True)
class WarriorFocusItem:
    candidate: MomentumCandidate
    float_provenance: FloatProvenance
    entry_trigger: Decimal | None
    stop_price: Decimal | None
    blocking_reasons: tuple[str, ...]
    market_data_stale: bool = False
    market_data_age_seconds: Decimal | None = None
    decision_timestamp: datetime | None = None
    decision_last: Decimal | None = None
    decision_bid: Decimal | None = None
    decision_ask: Decimal | None = None
    decision_spread_percent: Decimal | None = None


@dataclass(frozen=True, slots=True)
class WarriorPaperSnapshot:
    enabled: bool
    health: WarriorCaptureHealth
    configuration_fingerprint: str
    items: tuple[WarriorFocusItem, ...] = ()
    summary: WarriorPaperSummary = WarriorPaperSummary()
    metrics: CaptureMetrics | None = None
    last_error_type: str | None = None
    publication_rate_hz: Decimal = Decimal("0")
    capture_path: str | None = None
    observability_health: WarriorObservabilityHealth = WarriorObservabilityHealth.DISABLED
    entry_authorized: bool = False
    last_observation_at: datetime | None = None
    last_full_evaluation_at: datetime | None = None
    last_health_transition: WarriorHealthTransition | None = None


@dataclass(slots=True)
class _BarAccumulator:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def update(self, price: Decimal, volume: Decimal) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume

    def completed(self) -> MinuteBar:
        return MinuteBar(
            self.symbol, self.timestamp, self.open, self.high,
            self.low, self.close, self.volume,
        )


def strategy_configuration_fingerprint(
    config: WarriorMomentumConfig = WarriorMomentumConfig(),
) -> str:
    configuration = asdict(config)
    # Diagnostic destinations and session labels do not alter trading policy.
    # Excluding them keeps restarts and test-specific paths within the same
    # compatible strategy generation.
    for field_name in (
        "observability_enabled",
        "observability_root",
        "observability_session_id",
    ):
        configuration.pop(field_name, None)
    material = canonical_json({
        "strategy_version": STRATEGY_VERSION,
        "configuration": configuration,
    })
    return sha256(material.encode("utf-8")).hexdigest()


class WarriorDesktopSidecar:
    """Own capture state but no transport, trading client, or execution port."""

    def __init__(
        self, *, enabled: bool, storage_path: Path,
        environment: str = "UNKNOWN",
        strategy_config: WarriorMomentumConfig = WarriorMomentumConfig(),
        account_context_source: Callable[[], PaperAccountContext | None] | None = None,
        paper_entry_submitter: Callable[[object, int, Decimal], bool] | None = None,
        paper_exit_submitter: Callable[[str, int, Decimal, str, str | None], object] | None = None,
        paper_entry_canceller: Callable[[str], object] | None = None,
        paper_entry_replacer: Callable[..., object] | None = None,
        paper_entry_rearmer: Callable[..., object] | None = None,
        paper_position_quantity_source: Callable[[str], Decimal] | None = None,
        paper_execution_ownership_source: Callable[[str], bool] | None = None,
        paper_working_entry_source: Callable[[str, str], bool] | None = None,
        execution_quote_source: ExecutionQuoteSource | None = None,
        order_flow_client: object | None = None,
        research_observer: object | None = None,
        entry_value_observer: object | None = None,
        paper_campaign_id: str | None = None,
        taxonomy_execution_bridge: object | None = None,
        decision_intelligence_observer: object | None = None,
        paper_entry_intelligence: object | None = None,
        observability: object | None = None,
        report_worker_factory: Callable[..., WarriorReportWorker] = WarriorReportWorker,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.enabled = bool(enabled)
        self.storage_path = Path(storage_path)
        self.environment = str(environment).strip().upper() or "UNKNOWN"
        self.strategy_config = strategy_config
        self.capture_config = ForwardCaptureConfiguration(storage_path=self.storage_path)
        self.configuration_fingerprint = strategy_configuration_fingerprint(strategy_config)
        self._account_source = account_context_source or (lambda: None)
        self._paper_entry_submitter = paper_entry_submitter
        self._paper_exit_submitter = paper_exit_submitter
        self._paper_entry_canceller = paper_entry_canceller
        self._paper_entry_replacer = paper_entry_replacer
        self._paper_entry_rearmer = paper_entry_rearmer
        self._paper_position_quantity_source = paper_position_quantity_source
        self._paper_execution_ownership_source = paper_execution_ownership_source
        self._paper_working_entry_source = paper_working_entry_source
        self._execution_quote_source = execution_quote_source
        self._order_flow = OrderFlowPollingService(order_flow_client)
        self._research_observer = research_observer
        self._entry_value_observer = entry_value_observer
        self._paper_campaign_id = paper_campaign_id
        self._taxonomy_execution_bridge = taxonomy_execution_bridge
        self._decision_intelligence_observer = decision_intelligence_observer
        self._paper_entry_intelligence = paper_entry_intelligence
        self._observability = observability
        self._report_worker_factory = report_worker_factory
        self._accept_execution = False
        self._clock = clock
        self._lock = RLock()
        self._adapter: MarketEventScannerAdapter | None = None
        self._scanner_decision_source: Callable[[str], object | None] | None = None
        self._scanner_ranked_source: Callable[[str], bool] | None = None
        self._store: ForwardCaptureStore | None = None
        self._writer: ForwardCaptureWriter | None = None
        self._service: WarriorForwardCaptureService | None = None
        self._report_worker: WarriorReportWorker | None = None
        self._di_entry_diagnostic_keys: deque[tuple[str, str]] = deque(maxlen=1024)
        self._di_entry_diagnostic_key_set: set[tuple[str, str]] = set()
        self._last_report_metrics: ReportWorkerMetrics | None = None
        self._health = WarriorCaptureHealth.DISABLED if not enabled else WarriorCaptureHealth.STOPPED
        self._observability_health = WarriorObservabilityHealth.DISABLED
        self._last_error_type: str | None = None
        self._last_observation_at: datetime | None = None
        self._last_full_evaluation_at: datetime | None = None
        self._last_health_transition: WarriorHealthTransition | None = None
        self._last_diagnostic_drop_count = 0
        self._bars: dict[str, list[MinuteBar]] = {}
        self._accumulators: dict[str, _BarAccumulator] = {}
        self._last_volume: dict[str, Decimal] = {}
        self._latest: dict[str, MomentumCandidate] = {}
        # Diagnostic-only memory of the immediately preceding focus projection.
        # It is deliberately absent on the default no-op path.
        self._diagnostic_focus_symbols: set[str] | None = (
            set()
            if observability is not None and not isinstance(observability, NoOpWarriorObservabilitySink)
            else None
        )
        self._provenance: dict[str, FloatProvenance] = {}
        self._blocking: dict[str, tuple[str, ...]] = {}
        self._market_data_age: dict[str, Decimal | None] = {}
        self._market_data_timestamp: dict[str, datetime | None] = {}
        self._first_observed: set[str] = set()
        self._historical_preload_attempted: set[str] = set()
        self._stage_symbols: dict[str, set[str]] = {
            name: set() for name in (
                "discovered", "stocks_in_play", "near", "qualified",
                "setup_forming", "triggered", "entry_ready", "blocked",
            )
        }
        self._started_at: datetime | None = None
        self._run_key: str | None = None
        self._publications = 0
        self._publication_started = monotonic()
        self._daily_report: DailyForwardReport | None = None
        self._last_metrics: CaptureMetrics | None = None
        self._report_error_type: str | None = None
        self._protection_dirty: set[str] = set()
        self._last_protection_quantity: dict[str, int] = {}
        self._last_protection_attempt_at: dict[str, float] = {}
        self._last_intraminute_evaluation_at: dict[str, datetime] = {}
        self._last_session_policy_minute: datetime | None = None
        self._execution_recovery_orders: tuple[object, ...] = ()

    def bind_scanner_adapter(self, adapter: MarketEventScannerAdapter) -> None:
        if not isinstance(adapter, MarketEventScannerAdapter):
            raise TypeError("Warrior sidecar requires the shared scanner adapter")
        with self._lock:
            self._adapter = adapter

    def restore_execution_lifecycles(self, orders: Iterable[object]) -> None:
        """Stage authoritative active-campaign fills for prospective recovery."""
        with self._lock:
            self._execution_recovery_orders = tuple(orders)
            if self._service is not None:
                self._service.restore_execution_lifecycles(
                    self._execution_recovery_orders
                )

    def observe_paper_event(self, event: object) -> None:
        """Forward authoritative PAPER fills to research-only path capture."""
        with self._lock:
            if self._service is not None:
                self._service.observe_paper_event(event)

    def bind_scanner_decision_source(
        self, source: Callable[[str], object | None],
        ranked_source: Callable[[str], bool] | None = None,
    ) -> None:
        """Bind the production scanner projection as read-only capture context."""
        if not callable(source):
            raise TypeError("scanner decision source must be callable")
        with self._lock:
            self._scanner_decision_source = source
            self._scanner_ranked_source = ranked_source

    def needs_historical_preload(self, symbol: str) -> bool:
        """Return whether this process still needs a REST history attempt."""

        if not self.enabled:
            return False

        normalized = symbol.strip().upper()
        if not normalized:
            return False

        with self._lock:
            if normalized in self._historical_preload_attempted:
                return False

            existing = tuple(self._bars.get(normalized, ()))
            required = self._minimum_setup_history_bars()

            return len(contiguous_tail(existing)) < required

    def preload_historical_bars(
        self,
        symbol: str,
        bars: Iterable[object],
    ) -> int:
        """Merge completed REST candles into sidecar history.

        The method accepts chart HistoricalBar-compatible objects so broker
        composition does not need to depend on Warrior domain models.
        """

        if not self.enabled:
            return 0

        normalized = symbol.strip().upper()
        if not normalized:
            return 0

        now = self._aware_now()
        current_minute = now.replace(second=0, microsecond=0)

        incoming: list[MinuteBar] = []

        for value in bars:
            timestamp = getattr(value, "timestamp", None)
            opened = getattr(value, "open", None)
            high = getattr(value, "high", None)
            low = getattr(value, "low", None)
            close = getattr(value, "close", None)
            volume = getattr(value, "volume", None)

            if (
                timestamp is None
                or opened is None
                or high is None
                or low is None
                or close is None
                or volume is None
            ):
                continue

            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            else:
                timestamp = timestamp.astimezone(UTC)

            # The current minute can still be changing and must never become
            # historical setup evidence.
            if timestamp >= current_minute:
                continue

            try:
                candidate = MinuteBar(
                    normalized,
                    timestamp,
                    Decimal(opened),
                    Decimal(high),
                    Decimal(low),
                    Decimal(close),
                    Decimal(volume),
                )
            except (ArithmeticError, TypeError, ValueError):
                continue

            incoming.append(candidate)

        with self._lock:
            # REST responses may contain duplicate timestamps. Normalize them
            # before insertion so one completed candle is counted once.
            unique_incoming = {
                item.timestamp: item
                for item in incoming
            }

            if unique_incoming:
                self._historical_preload_attempted.add(normalized)

            existing = {
                item.timestamp: item
                for item in self._bars.get(normalized, ())
            }

            before = set(existing)

            existing.update(unique_incoming)

            merged = tuple(
                sorted(
                    existing.values(),
                    key=lambda item: item.timestamp,
                )
            )[-120:]

            self._bars[normalized] = list(merged)

            return sum(
                1
                for timestamp in unique_incoming
                if timestamp not in before
            )

    def _minimum_setup_history_bars(self) -> int:
        setup = self.strategy_config.setups

        return max(
            5,
            3 + setup.minimum_pullback_bars + 1,
            4 + setup.minimum_consolidation_bars + 1,
            setup.flat_top_tests + 3,
        )


    def start(self, environment: str | None = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._health in {WarriorCaptureHealth.STARTING, WarriorCaptureHealth.RUNNING}:
                return
            self._health = WarriorCaptureHealth.STARTING
            if environment:
                self.environment = environment.strip().upper()
            try:
                entry_value_start = getattr(
                    self._entry_value_observer, "start", None,
                )
                if callable(entry_value_start):
                    entry_value_start(self.environment)
                intelligence_start = getattr(
                    self._decision_intelligence_observer, "start", None,
                )
                if callable(intelligence_start):
                    intelligence_start(self.environment)
                self._store = ForwardCaptureStore(self.storage_path)
                self._writer = ForwardCaptureWriter(
                    self._store, capacity=self.capture_config.queue_capacity,
                    batch_size=self.capture_config.batch_size,
                    flush_interval_seconds=self.capture_config.flush_interval_seconds,
                    configuration_fingerprint=self.configuration_fingerprint,
                )
                self._service = WarriorForwardCaptureService(
                    self._store, self._writer, self.strategy_config,
                    self.capture_config,
                    configuration_fingerprint=self.configuration_fingerprint,
                    paper_entry_submitter=self._paper_entry_submitter,
                    paper_exit_submitter=self._paper_exit_submitter,
                    paper_entry_replacer=self._paper_entry_replacer,
                    paper_entry_rearmer=self._paper_entry_rearmer,
                    paper_position_quantity_source=self._paper_position_quantity_source,
                    paper_execution_ownership_source=self._paper_execution_ownership_source,
                    paper_working_entry_source=self._paper_working_entry_source,
                    execution_quote_source=self._execution_quote_source,
                    execution_permitted=lambda: self._accept_execution,
                    account_refresh_source=self._account_source,
                    entry_value_observer=getattr(
                        self._entry_value_observer, "observe_decision", None,
                    ),
                    paper_campaign_id=self._paper_campaign_id,
                    taxonomy_execution_bridge=self._taxonomy_execution_bridge,
                    decision_intelligence_observer=getattr(
                        self._decision_intelligence_observer, "observe_decision", None,
                    ) if self._paper_entry_intelligence is None else None,
                    decision_intelligence_entry_observer=(
                        self._observe_decision_and_entry
                        if self._paper_entry_intelligence is not None
                        and callable(getattr(self._decision_intelligence_observer, "observe_decision", None))
                        else None
                    ),
                    paper_entry_intelligence=getattr(
                        self._paper_entry_intelligence, "assess", None,
                    ) if self.environment.upper() == "PAPER" else None,
                )
                if self._execution_recovery_orders:
                    self._service.restore_execution_lifecycles(
                        self._execution_recovery_orders
                    )
                self._restore_bars()
                now = self._aware_now()
                self._started_at = now
                self._run_key = sha256(
                    f"{self.configuration_fingerprint}|{self.environment}|{now.isoformat()}".encode()
                ).hexdigest()
                self._writer.submit(self._session_record("START", now))
                self._report_worker = self._report_worker_factory(
                    self._store,
                    report_sink=self._accept_report,
                    failure_sink=self._record_report_failure,
                )
                self._request_report_refresh(now.astimezone(EASTERN).date())
                performance_diagnostics.set_diagnostic_sink(
                    self._persist_latency_diagnostic
                )
                self._set_strategy_health(
                    WarriorCaptureHealth.RUNNING,
                    category="LIFECYCLE", reason="STARTED",
                )
                self._set_observability_health(
                    WarriorObservabilityHealth.RUNNING,
                    reason="DIAGNOSTIC_QUEUE_AVAILABLE",
                )
                self._accept_execution = True
                self._order_flow.start()
            except Exception as exc:
                self._last_error_type = type(exc).__name__
                self._accept_execution = False
                self._set_strategy_health(
                    WarriorCaptureHealth.DEGRADED,
                    category="CRITICAL_STARTUP_FAILURE",
                    reason="START_FAILED", exception=exc,
                )
                entry_value_stop = getattr(
                    self._entry_value_observer, "close", None,
                )
                if callable(entry_value_stop):
                    try:
                        entry_value_stop()
                    except Exception:
                        pass
                intelligence_stop = getattr(
                    self._decision_intelligence_observer, "close", None,
                )
                if callable(intelligence_stop):
                    try:
                        intelligence_stop()
                    except Exception:
                        pass

    def stop(self) -> None:
        if not self.enabled:
            return
        # Publish shutdown intent before waiting for an in-flight confirmation.
        self._accept_execution = False
        self._order_flow.stop()
        with self._lock:
            writer, store = self._writer, self._store
            if writer is None or store is None:
                self._health = WarriorCaptureHealth.STOPPED
                self._observability_health = WarriorObservabilityHealth.DISABLED
                entry_value_stop = getattr(
                    self._entry_value_observer, "close", None,
                )
                if callable(entry_value_stop):
                    try:
                        entry_value_stop()
                    except Exception:
                        pass
                intelligence_stop = getattr(
                    self._decision_intelligence_observer, "close", None,
                )
                if callable(intelligence_stop):
                    try:
                        intelligence_stop()
                    except Exception:
                        pass
                return
            lifecycle_phase = "shadow outcome finalization"
            try:
                now = self._aware_now()
                if self._service is not None:
                    self._service.shutdown_intraminute_shadow(now)
                    self._service.finalize_shadow_outcomes(now)
                lifecycle_phase = "shadow/capture writer drain"
                self._flush_capture_writer(writer)
                lifecycle_phase = "shadow daily report finalization"
                report_worker = self._report_worker
                if report_worker is not None:
                    self._request_report_refresh(
                        now.astimezone(EASTERN).date(), persist=True
                    )
                    report_worker.close(timeout_seconds=5.0)
                    self._last_report_metrics = report_worker.metrics()
                lifecycle_phase = "shadow session finalization"
                writer.submit(self._session_record("END", now))
                lifecycle_phase = "shadow/capture writer close"
                writer.close()
                lifecycle_phase = "Warrior runtime finalization"
                self._last_metrics = writer.metrics()
                self._health = WarriorCaptureHealth.STOPPED
                self._observability_health = WarriorObservabilityHealth.DISABLED
            except Exception as exc:
                self._last_error_type = type(exc).__name__
                self._health = WarriorCaptureHealth.DEGRADED
                log_runtime_exception(
                    _RUNTIME_LOGGER,
                    exc,
                    event_type="runtime_cleanup_exception",
                    lifecycle_phase=lifecycle_phase,
                    shutdown_requested=True,
                )
                raise
            finally:
                entry_value_stop = getattr(
                    self._entry_value_observer, "close", None,
                )
                if callable(entry_value_stop):
                    try:
                        entry_value_stop()
                    except Exception:
                        pass
                intelligence_stop = getattr(
                    self._decision_intelligence_observer, "close", None,
                )
                if callable(intelligence_stop):
                    try:
                        intelligence_stop()
                    except Exception:
                        pass
                performance_diagnostics.set_diagnostic_sink(None)
                self._report_worker = None
                self._writer = None
                self._service = None

    def __call__(self, event: MarketEvent) -> None:
        if not self.enabled:
            return
        lock_started = perf_counter()
        self._lock.acquire()
        performance_diagnostics.record_component_duration(
            "warrior.event_lock_wait",
            (perf_counter() - lock_started) * 1000.0,
            event_type="GUI_REFRESH",
        )
        reconcile_service = None
        reconcile_symbol: str | None = None
        try:
            if self._health in {
                WarriorCaptureHealth.DISABLED,
                WarriorCaptureHealth.STARTING,
                WarriorCaptureHealth.STOPPED,
            }:
                return
            self._last_observation_at = self._aware_now()
            self._update_health(allow_recovery=False)
            try:
                self._apply_session_policy(event.timestamp)
                if event.symbol is not None and self._service is not None:
                    normalized = event.symbol.strip().upper()
                    if self._protection_reconciliation_due(normalized):
                        reconcile_service = self._service
                        reconcile_symbol = normalized
                # Strategy processing remains reachable while new-entry
                # authority is fail-closed.  The first successful callback is
                # therefore a recovery/protection pass, not a new-risk pass.
                self._consume(event)
                self._update_health(allow_recovery=True)
            except Exception as exc:
                self._last_error_type = type(exc).__name__
                self._accept_execution = False
                self._set_strategy_health(
                    WarriorCaptureHealth.DEGRADED,
                    category="CRITICAL_STRATEGY_FAILURE",
                    reason="MARKET_PROCESSING_FAILED", exception=exc,
                )
        finally:
            self._lock.release()

        if reconcile_service is None or reconcile_symbol is None:
            return
        protection_started = perf_counter()
        protection_success = False
        performance_diagnostics.increment_reconciliation_counter("executions")
        try:
            protection_success = bool(
                reconcile_service.reconcile_authoritative_protection(
                    reconcile_symbol, event.timestamp,
                )
            )

        except Exception as exc:
            with self._lock:
                self._protection_dirty.add(reconcile_symbol)
                self._last_error_type = type(exc).__name__
                self._accept_execution = False
                self._set_strategy_health(
                    WarriorCaptureHealth.DEGRADED,
                    category="PROTECTION_FAILURE",
                    reason="PROTECTION_RECONCILIATION_FAILED", exception=exc,
                )
        finally:
            performance_diagnostics.increment_reconciliation_counter(
                "successes" if protection_success else "failures"
            )
            with self._lock:
                if protection_success:
                    self._protection_dirty.discard(reconcile_symbol)
                else:
                    self._protection_dirty.add(reconcile_symbol)
            performance_diagnostics.record_component_duration(
                "warrior.protection_reconciliation",
                (perf_counter() - protection_started) * 1000.0,
                event_type=getattr(getattr(event, "event_type", None), "value", None),
                symbol=reconcile_symbol,
                success=protection_success,
            )

    def _apply_session_policy(self, observed_at: datetime) -> None:
        config = self.strategy_config.session_management
        if not config.enabled or self._service is None:
            return
        minute = observed_at.replace(second=0, microsecond=0)
        if minute == self._last_session_policy_minute:
            return
        self._last_session_policy_minute = minute
        if entry_cutoff_reached(observed_at, config) and self._paper_entry_canceller is not None:
            try:
                self._paper_entry_canceller("SESSION_ENTRY_CUTOFF")
            except Exception:
                self._last_error_type = "SessionEntryCancellationFailed"
        if flatten_window_reached(observed_at, config):
            self._service.manage_session_boundary(observed_at)

    def session_policy_tick(self, observed_at: datetime | None = None) -> None:
        """Run the boundary policy even when the market stream is quiet."""
        if not self.enabled:
            return
        with self._lock:
            if self._service is not None:
                self._apply_session_policy(observed_at or self._aware_now())

    def overnight_capability_lost(self, observed_at: datetime | None = None) -> None:
        """Fail closed for carried positions when entitlement is denied."""
        with self._lock:
            if self._service is not None:
                self._service.flatten_for_overnight_capability_loss(
                    observed_at or self._aware_now(),
                )

    def _protection_reconciliation_due(self, symbol: str) -> bool:
        """Return whether a protection audit is needed without doing I/O."""
        performance_diagnostics.increment_reconciliation_counter(
            "eligibility_checks"
        )
        source = self._paper_position_quantity_source
        if source is None:
            return False
        quantity = max(0, int(source(symbol)))
        previous = self._last_protection_quantity.get(symbol)
        self._last_protection_quantity[symbol] = quantity
        if quantity <= 0:
            self._protection_dirty.discard(symbol)
            self._last_protection_attempt_at.pop(symbol, None)
            return False

        now = monotonic()
        changed = previous != quantity
        if changed:
            performance_diagnostics.increment_reconciliation_counter(
                "quantity_change_triggers"
            )
            self._protection_dirty.add(symbol)
        last_attempt = self._last_protection_attempt_at.get(symbol)
        periodic_due = (
            last_attempt is None
            or now - last_attempt >= _PROTECTION_AUDIT_INTERVAL_SECONDS
        )
        if periodic_due:
            performance_diagnostics.increment_reconciliation_counter(
                "periodic_audit_triggers"
            )
            self._protection_dirty.add(symbol)
        if symbol not in self._protection_dirty:
            return False
        if not changed and not periodic_due:
            performance_diagnostics.increment_reconciliation_counter("dirty_triggers")
            return False
        self._last_protection_attempt_at[symbol] = now
        return True

    def snapshot(self) -> WarriorPaperSnapshot:
        lock_started = perf_counter()
        self._lock.acquire()
        performance_diagnostics.record_component_duration(
            "warrior.gui_snapshot_lock_wait",
            (perf_counter() - lock_started) * 1000.0,
            event_type="GUI_REFRESH",
        )
        try:
            writer = self._writer
            metrics = self._last_metrics if writer is None else writer.metrics()
            if metrics is not None:
                self._last_metrics = metrics
            candidates = tuple(self._latest.values())
            runtime = (
                self._service.runtime
                if self._service is not None
                else WarriorMomentumRuntime(self.strategy_config)
            )
            health = self._health
            last_error = self._last_error_type
            observability_health = self._observability_health
            entry_authorized = self._accept_execution
            last_observation_at = self._last_observation_at
            last_full_evaluation_at = self._last_full_evaluation_at
            last_health_transition = self._last_health_transition
            enabled = self.enabled
            configuration_fingerprint = self.configuration_fingerprint
            report = self._daily_report
            stage_counts = {
                name: len(values) for name, values in self._stage_symbols.items()
            }
            open_paper_trades = (
                (0 if report is None else report.open_paper_positions)
                if self._service is None else len(self._service.open_paper_symbols)
            )
            counterfactuals = (
                0 if self._service is None else len(self._service.counterfactual_symbols)
            )
            prior_focus_symbols = self._diagnostic_focus_symbols
        finally:
            self._lock.release()

        ranked = tuple(runtime.rank(candidates))
        ranked_symbols = {item.symbol.strip().upper() for item in ranked}
        if prior_focus_symbols is not None:
            for item in ranked:
                _safe_warrior_observe(
                    self._observability, "FOCUS_PROJECTION_INSERT", item.symbol,
                    focus_inserted=True, focus_rank=item.rank, focus_size=len(ranked),
                )
            for symbol in {item.symbol.strip().upper() for item in candidates}:
                if symbol not in ranked_symbols:
                    focus_action = (
                        "DROPPED_FROM_PRIOR_SNAPSHOT"
                        if prior_focus_symbols is not None and symbol in prior_focus_symbols
                        else "TOP_N_OMITTED"
                    )
                    _safe_warrior_observe(
                        self._observability, "FOCUS_PROJECTION_OMIT", symbol,
                        focus_inserted=False, focus_action=focus_action,
                        focus_size=len(ranked),
                    )
            with self._lock:
                self._diagnostic_focus_symbols = set(ranked_symbols)
        summary = WarriorPaperSummary(
            discovered=stage_counts["discovered"],
            stocks_in_play=stage_counts["stocks_in_play"],
            near=stage_counts["near"],
            qualified=stage_counts["qualified"],
            setup_forming=stage_counts["setup_forming"],
            triggered=stage_counts["triggered"],
            entry_ready=stage_counts["entry_ready"],
            open_paper_trades=open_paper_trades,
            today_paper_r=None if report is None else report.total_r,
            today_trades=0 if report is None else report.paper_trades,
            triggered_but_blocked=stage_counts["blocked"],
            tracked_counterfactuals=counterfactuals,
        )
        elapsed = max(monotonic() - self._publication_started, 1e-9)
        return WarriorPaperSnapshot(
            enabled, health, configuration_fingerprint,
            tuple(self._focus_item(item) for item in ranked), summary,
            metrics, last_error,
            Decimal(str(self._publications / elapsed)),
            str(self.storage_path.resolve()),
            observability_health,
            entry_authorized,
            last_observation_at,
            last_full_evaluation_at,
            last_health_transition,
        )

    def set_research_observer(self, observer: object | None) -> None:
        """Replace the advisory observer without changing Warrior authority."""
        self._research_observer = observer

    def management_context(self, symbol: str) -> dict[str, object] | None:
        """Return the active Warrior management facts for read-only GUI use."""
        normalized = symbol.strip().upper()
        with self._lock:
            service = self._service
            state = None if service is None else service._paper.get(normalized)
            if state is None:
                return None
            return {
                "entry_price": state.entry_price,
                "structural_stop": state.signal.stop_price,
                "stop": state.stop,
                "target_levels": state.signal.target_levels,
                "first_taken": state.first_taken,
                "second_taken": state.second_taken,
            }

    def adaptive_entry_context(
        self, symbol: str, cutoff: datetime,
    ) -> dict[str, object] | None:
        """Return one cutoff-safe, read-only Warrior research snapshot."""

        normalized = symbol.strip().upper()
        # Research must never wait behind authoritative Warrior processing.
        # A missed snapshot is explicit insufficient evidence on this event;
        # the next market event can retry.
        if not self._lock.acquire(blocking=False):
            return None
        try:
            candidate = self._latest.get(normalized)
            if candidate is None or candidate.timestamp > cutoff:
                return None
            setup = candidate.setup
            return {
                "observed_at": candidate.timestamp,
                "scanner_rank": candidate.rank,
                "scanner_score": candidate.score.total,
                "relative_volume": candidate.relative_volume,
                "percentage_change": candidate.percentage_change,
                "volume": candidate.volume,
                "dollar_volume": candidate.dollar_volume,
                "float_shares": candidate.float_shares,
                "warrior_current_state": candidate.status.value,
                "setup_type": None if setup is None else setup.setup_type.value,
                "setup_state": None if setup is None else setup.state.value,
                "current_reference_price": None if setup is None else setup.trigger,
                "current_structural_stop": None if setup is None else setup.stop_price,
                "current_setup_quality": None if setup is None else setup.score,
                "current_technical_actionable": bool(
                    setup is not None
                    and setup.state is SetupState.TRIGGERED
                    and candidate.tradable
                    and not candidate.halted
                ),
                "distance_from_hod_percent": candidate.distance_from_hod_percent,
            }
        finally:
            self._lock.release()

    def mark_gui_refresh(self) -> None:
        with self._lock:
            if self._writer is not None:
                self._writer.record_gui_refresh()

    def retained_symbols(self) -> tuple[str, ...]:
        with self._lock:
            return () if self._service is None else self._service.open_paper_symbols

    def _consume(self, event: MarketEvent) -> None:
        adapter, service = self._adapter, self._service
        if adapter is None or service is None or event.symbol is None:
            return
        symbol = event.symbol.strip().upper()
        lookup_started = perf_counter()
        observation = adapter.observation_for(symbol)
        performance_diagnostics.record_component_duration(
            "warrior.observation_lookup",
            (perf_counter() - lookup_started) * 1000.0,
            event_type=getattr(getattr(event, "event_type", None), "value", None),
            symbol=symbol,
        )
        if observation is None:
            # Retained PAPER positions are management authority even when the
            # scanner cannot assemble a discovery observation (for example,
            # missing reference data or accumulated volume after recovery).
            # Advance only from a real trade price or the adapter's retained
            # last trade; never synthesize an executable mark from bid/ask.
            if symbol in service.open_paper_symbols:
                state = adapter.state_for(symbol)
                retained_mark = (
                    event.payload.price
                    if (
                        event.event_type is MarketEventType.TRADE
                        and isinstance(event.payload, TradePayload)
                    )
                    else (
                        None
                        if state is None else state.last_price
                    )
                )
                completed = self._complete_elapsed_bar(event)
                if (
                    retained_mark is not None
                    and event.event_type in {
                        MarketEventType.QUOTE, MarketEventType.TRADE,
                    }
                ):
                    completed = (
                        self._aggregate_retained_mark(event, retained_mark)
                        or completed
                    )
                if completed:
                    observed_at = self._aware_now()
                    service.invalidate_intraminute_shadow(
                        symbol,
                        event.timestamp,
                        ShadowLatchedTransition.NEW_BAR_INVALIDATION,
                        reason="NEW_COMPLETED_RETAINED_MANAGEMENT_BAR",
                        processing_time=observed_at,
                    )
                    # Aggregators return a completion flag and append
                    # the authoritative immutable bar to the completed store.
                    management_bar = self._bars[symbol][-1]
                    service.observe_market_bar(
                        symbol, management_bar, observed_at,
                    )
                    if self._writer is not None:
                        self._flush_capture_writer(self._writer)
                    self._request_report_refresh(
                        event.timestamp.astimezone(EASTERN).date()
                    )
            return
        # A completed minute is a wall-clock boundary, not a dependency on
        # receiving the first qualifying TRADE_SIZE event of the next minute.
        # Quotes (or other later market events) must be allowed to close the
        # prior trade accumulator so retained open-position management keeps
        # advancing even when the feed becomes quote-heavy.
        completed = self._complete_elapsed_bar(event)
        qualifying_trade = (
            event.event_type is MarketEventType.TRADE
            and isinstance(event.payload, TradePayload)
            and event.payload.volume_semantics is VolumeSemantics.TRADE_SIZE
            and not event.payload.trade_id.startswith("snapshot")
        )
        if qualifying_trade:
            aggregate_started = perf_counter()
            completed = (
                self._aggregate_trade(event, observation.current_volume)
                or completed
            )
            performance_diagnostics.record_component_duration(
                "warrior.trade_aggregation",
                (perf_counter() - aggregate_started) * 1000.0,
                event_type="TRADE",
                symbol=symbol,
            )
        elif (
            symbol in service.open_paper_symbols
            and event.event_type in {MarketEventType.QUOTE, MarketEventType.TRADE}
            and observation.price is not None
        ):
            # Recovered positions must keep receiving completed management
            # bars when after-hours traffic is quote-only.  A shared-adapter
            # last price shapes OHLC but never invents traded volume.
            completed = (
                self._aggregate_retained_mark(event, observation.price)
                or completed
            )
        if completed:
            service.invalidate_intraminute_shadow(
                symbol,
                observation.timestamp,
                ShadowLatchedTransition.NEW_BAR_INVALIDATION,
                reason="NEW_COMPLETED_BAR",
                processing_time=self._aware_now(),
            )
        scheduling_timestamp = event.received_timestamp or event.timestamp
        intraminute_due = False
        if symbol in self._first_observed and not completed:
            prior_candidate = self._latest.get(symbol)
            prior_setup = None if prior_candidate is None else prior_candidate.setup
            structurally_triggered = (
                prior_setup is not None
                and prior_setup.state is SetupState.TRIGGERED
            )
            active_candidate = bool(
                prior_candidate is not None
                and (
                    prior_candidate.discovery_qualified
                    or prior_candidate.status in {
                        CandidateStatus.NEAR_QUALIFIED,
                        CandidateStatus.QUALIFIED,
                        CandidateStatus.SETUP_FORMING,
                        CandidateStatus.ENTRY_READY,
                        CandidateStatus.AWAITING_EXECUTION_DATA,
                    }
                )
            )
            last_intraminute = self._last_intraminute_evaluation_at.get(symbol)
            # Quote, trade, and retained-snapshot source timestamps are
            # independent Webull timelines.  A snapshot can therefore be
            # later than a subsequently received live quote without either
            # event being stale.  Cadence is a local processing concern, so
            # schedule reevaluations on receipt time and retain provider time
            # exclusively for per-channel ordering and market-data safety.
            comparison_timestamp = (
                last_intraminute
                if last_intraminute is not None
                else (None if prior_candidate is None else prior_candidate.timestamp)
            )
            elapsed = (
                None
                if comparison_timestamp is None
                else (scheduling_timestamp - comparison_timestamp).total_seconds()
            )
            active_refresh_due = bool(
                active_candidate
                and elapsed is not None
                and (
                    elapsed >= _ACTIVE_CANDIDATE_MAX_REEVALUATION_SECONDS
                    or (
                        elapsed >= _ACTIVE_CANDIDATE_MIN_REEVALUATION_SECONDS
                        and _meaningful_active_candidate_change(
                            prior_candidate, observation,
                        )
                    )
                )
            )
            intraminute_due = bool(
                (structurally_triggered or active_refresh_due)
                and event.event_type in {MarketEventType.QUOTE, MarketEventType.TRADE}
                and prior_candidate is not None
                and (
                    active_refresh_due
                    or elapsed is None
                    or elapsed >= _INTRAMINUTE_REEVALUATION_SECONDS
                )
            )
            if intraminute_due:
                # Reserve the slot before evaluation so an exception cannot
                # create an unbounded retry loop on a hot symbol.
                self._last_intraminute_evaluation_at[symbol] = scheduling_timestamp
        if symbol not in self._first_observed or completed or intraminute_due:
            # Anchor every full evaluation in the same local cadence domain,
            # including the initial and completed-bar evaluations.
            self._last_intraminute_evaluation_at[symbol] = scheduling_timestamp
            available_bars = tuple(self._bars.get(symbol, ())[-120:])
            evaluated_at = self._aware_now()
            decision_session = scanner_session(evaluated_at).value
            history = canonical_completed_history(
                available_bars, observation.timestamp,
                session=decision_session,
            )
            # Historical preload is useful only when the live bar stream has
            # reached the same point in time.  A stale preload must not make a
            # current decision appear to have fresh setup history.
            if not current_completed_bar_tail(available_bars, observation.timestamp):
                history = ()
            quote_freshness = last_price_freshness = None
            state = adapter.state_for(symbol)
            processing_age = (
                None
                if event.received_timestamp is None
                else Decimal(str(max(
                    0,
                    (evaluated_at - event.received_timestamp).total_seconds(),
                )))
            )
            delivery_age = Decimal(str(max(
                0,
                (evaluated_at - event.timestamp).total_seconds(),
            )))
            if state is not None and state.quote_timestamp is not None:
                quote_freshness = Decimal(str(max(
                    0, (evaluated_at - state.quote_timestamp).total_seconds(),
                )))
            if state is not None and state.last_price_timestamp is not None:
                last_price_freshness = Decimal(str(max(
                    0, (evaluated_at - state.last_price_timestamp).total_seconds(),
                )))
            provenance = (
                FloatProvenance.MARKET_CAP_PRICE_PROXY
                if observation.float_shares is not None else FloatProvenance.UNKNOWN
            )
            scanner_decision = (
                None if self._scanner_decision_source is None
                else self._scanner_decision_source(symbol)
            )
            scanner_classification = None
            if scanner_decision is not None:
                scanner_classification = _scanner_classification(
                    scanner_decision,
                    False if self._scanner_ranked_source is None
                    else self._scanner_ranked_source(symbol),
                )
            flow_started = perf_counter()
            order_flow = self._order_flow.assessment(symbol, now=evaluated_at)
            performance_diagnostics.record_component_duration(
                "warrior.order_flow_cache_assessment",
                (perf_counter() - flow_started) * 1000.0,
                event_type=getattr(getattr(event, "event_type", None), "value", None),
                symbol=symbol,
            )
            point_in_time = PointInTimeObservation(
                    observation, decision_session,
                    history, float_provenance=provenance,
                    catalyst_event_timestamp=observation.catalyst_published_at,
                    catalyst_event_date=(
                        None
                        if observation.catalyst_published_at is None
                        else observation.catalyst_published_at.date()
                    ),
                    catalyst_source=(
                        observation.catalyst_source
                        or "UNATTRIBUTED_CATALYST_EVIDENCE"
                    ),
                    catalyst_source_classification=(
                        "AGGREGATED_PRODUCTION_EVIDENCE"
                        if observation.catalyst_source
                        else "PRODUCTION_MARKET_DATA"
                    ),
                    quote_observed_at=(None if state is None else state.quote_timestamp),
                    quote_freshness_seconds=quote_freshness,
                    last_price_observed_at=(
                        None if state is None else state.last_price_timestamp
                    ),
                    last_price_freshness_seconds=last_price_freshness,
                    processing_age_seconds=processing_age,
                    delivery_age_seconds=delivery_age,
                    evaluation_timestamp=evaluated_at,
                    halt_state_known=True,
                    volume_known=True,
                    historical_bars_available=bool(history),
                    scanner_rank=(
                        None if scanner_decision is None
                        else getattr(scanner_decision, "scanner_rank", None)
                    ),
                    scanner_score=(
                        None if scanner_decision is None
                        else getattr(scanner_decision, "score", None)
                    ),
                    scanner_classification=scanner_classification,
                    scanner_failed_rules=(
                        () if scanner_decision is None
                        else tuple(getattr(scanner_decision, "failed_rules", ()))
                    ),
                    best_bid_size=(None if state is None else state.bid_size),
                    best_ask_size=(None if state is None else state.ask_size),
                    depth_bids=(
                        event.payload.bids
                        if isinstance(event.payload, QuotePayload) else ()
                    ),
                    depth_asks=(
                        event.payload.asks
                        if isinstance(event.payload, QuotePayload) else ()
                    ),
                    order_flow=order_flow,
                    quote_provenance="SHARED_SCANNER_ADAPTER",
                )
            service_started = perf_counter()
            service_success = False
            try:
                candidate, signal = service.observe(
                    point_in_time,
                    account=self._account_source(),
                )
                self._last_full_evaluation_at = evaluated_at
                service_success = True
                _safe_warrior_observe(
                    self._observability, "WARRIOR_EVALUATOR_INVOKED", symbol,
                    evaluator_invoked=True,
                    decision_result=("ENTRY_READY" if signal is not None else candidate.status.value),
                    session=point_in_time.session,
                )
            except Exception as error:
                _safe_warrior_observe(
                    self._observability, "WARRIOR_EVALUATOR_INVOKED", symbol,
                    evaluator_invoked=True, decision_result="ERROR",
                    exception_class=type(error).__name__, session=point_in_time.session,
                )
                raise
            finally:
                performance_diagnostics.record_component_duration(
                    "warrior.service_observe",
                    (perf_counter() - service_started) * 1000.0,
                    event_type=getattr(getattr(event, "event_type", None), "value", None),
                    symbol=symbol,
                    success=service_success,
                )
            processing_delayed = bool(
                (
                    processing_age is not None
                    and processing_age > self.capture_config.quote_stale_after_seconds
                )
                or delivery_age > self.capture_config.quote_stale_after_seconds
            )
            performance_diagnostics.record_execution_safety(
                processing_delayed=processing_delayed,
                entry_authorized=signal is not None,
                execution_quote_requested=False,
                paper_order_created=signal is not None,
            )
            prior_latest = self._latest.get(symbol)
            accepted_latest = prior_latest is None or candidate.timestamp >= prior_latest.timestamp
            if accepted_latest:
                self._latest[symbol] = candidate
                _safe_warrior_observe(
                    self._observability, "FOCUS_PROJECTION_REPLACE", symbol,
                    focus_action="REPLACE_EXISTING" if prior_latest is not None else "FIRST_INSERT",
                    session=candidate.session,
                )
                self._provenance[symbol] = provenance
                self._blocking[symbol] = _blocking_reasons(candidate, signal is not None)
                ages = tuple(
                    age for age in (quote_freshness, last_price_freshness)
                    if age is not None
                )
                self._market_data_age[symbol] = max(ages) if len(ages) == 2 else None
                market_timestamps = tuple(
                    timestamp for timestamp in (
                        None if state is None else state.quote_timestamp,
                        None if state is None else state.last_price_timestamp,
                    ) if timestamp is not None
                )
                self._market_data_timestamp[symbol] = (
                    min(market_timestamps) if len(market_timestamps) == 2 else None
                )
            self._observe_stages(candidate, signal is not None)
            if accepted_latest:
                self._update_order_flow_priority(symbol, candidate, signal is not None)
            research_decision = getattr(
                self._research_observer, "observe_warrior_decision", None,
            )
            if callable(research_decision):
                research_started = perf_counter()
                try:
                    research_decision(point_in_time, candidate, signal)
                except Exception:
                    pass
                finally:
                    performance_diagnostics.record_component_duration(
                        "warrior.capture_research_callback",
                        (perf_counter() - research_started) * 1000.0,
                        event_type=getattr(getattr(event, "event_type", None), "value", None),
                        symbol=symbol,
                    )
            self._first_observed.add(symbol)
            self._publications += 1
            if signal is not None or completed:
                assert self._writer is not None
                self._flush_capture_writer(self._writer)
                self._request_report_refresh(
                    observation.timestamp.astimezone(EASTERN).date()
                )
        else:
            state = adapter.state_for(symbol)
            evaluated_at = self._aware_now()
            # Market freshness is live-state, not decision-state. Keep the GUI
            # projection current on the lightweight shadow path so avoiding a
            # full research pass does not falsely display stale entry data.
            if state is not None:
                freshness_ages = tuple(
                    Decimal(str(max(
                        0, (evaluated_at - timestamp).total_seconds(),
                    )))
                    for timestamp in (
                        state.quote_timestamp,
                        state.last_price_timestamp,
                    )
                    if timestamp is not None
                )
                self._market_data_age[symbol] = (
                    max(freshness_ages) if len(freshness_ages) == 2 else None
                )
                freshness_timestamps = tuple(
                    timestamp
                    for timestamp in (
                        state.quote_timestamp,
                        state.last_price_timestamp,
                    )
                    if timestamp is not None
                )
                self._market_data_timestamp[symbol] = (
                    min(freshness_timestamps)
                    if len(freshness_timestamps) == 2 else None
                )
            service.observe_intraminute_shadow(ShadowMarketObservation(
                symbol=symbol,
                observed_at=evaluated_at,
                last=observation.price,
                bid=observation.bid,
                ask=observation.ask,
                last_timestamp=(
                    observation.last_price_timestamp
                    if state is None else state.last_price_timestamp
                ),
                quote_timestamp=(
                    observation.quote_timestamp
                    if state is None else state.quote_timestamp
                ),
                last_received_timestamp=(
                    observation.last_price_received_timestamp
                    if state is None else state.last_price_received_timestamp
                ),
                quote_received_timestamp=(
                    observation.quote_received_timestamp
                    if state is None else state.quote_received_timestamp
                ),
                halted=observation.halted,
                tradable=observation.tradable,
                session=scanner_session(evaluated_at).value,
                execution_permitted=self._accept_execution,
            ))

    def _update_order_flow_priority(
        self, symbol: str, candidate: MomentumCandidate, entry_ready: bool,
    ) -> None:
        position_quantity = Decimal("0")
        if self._paper_position_quantity_source is not None:
            try:
                position_quantity = Decimal(str(self._paper_position_quantity_source(symbol)))
            except (ArithmeticError, TypeError, ValueError):
                position_quantity = Decimal("0")
        if entry_ready or position_quantity != 0:
            priority = OrderFlowPriority.HIGH
        elif symbol in self._stage_symbols["setup_forming"]:
            priority = OrderFlowPriority.MEDIUM
        else:
            priority = OrderFlowPriority.LOW
        self._order_flow.update_symbol(symbol, priority)

    def _complete_elapsed_bar(self, event: MarketEvent) -> bool:
        """Finalize a prior trade bar when any later event crosses its minute."""
        if event.symbol is None:
            return False
        symbol = event.symbol.strip().upper()
        current = self._accumulators.get(symbol)
        if current is None:
            return False
        minute = event.timestamp.replace(second=0, microsecond=0)
        if minute <= current.timestamp:
            return False
        self._bars.setdefault(symbol, []).append(current.completed())
        self._bars[symbol] = self._bars[symbol][-120:]
        self._accumulators.pop(symbol, None)
        return True

    def _aggregate_retained_mark(
        self, event: MarketEvent, price: Decimal,
    ) -> bool:
        """Build a zero-volume management bar from a retained live mark."""
        assert event.symbol is not None
        symbol = event.symbol.strip().upper()
        minute = event.timestamp.replace(second=0, microsecond=0)
        current = self._accumulators.get(symbol)
        completed = False
        if current is not None and minute > current.timestamp:
            self._bars.setdefault(symbol, []).append(current.completed())
            self._bars[symbol] = self._bars[symbol][-120:]
            completed = True
            current = None
        if current is None:
            self._accumulators[symbol] = _BarAccumulator(
                symbol, minute, price, price, price, price, Decimal("0"),
            )
        elif minute == current.timestamp:
            current.update(price, Decimal("0"))
        return completed

    def _aggregate_trade(self, event: MarketEvent, cumulative: Decimal) -> bool:
        assert event.symbol is not None and isinstance(event.payload, TradePayload)
        symbol = event.symbol.strip().upper()
        minute = event.timestamp.replace(second=0, microsecond=0)
        prior_total = self._last_volume.get(symbol, cumulative)
        delta = max(Decimal("0"), cumulative - prior_total)
        self._last_volume[symbol] = cumulative
        current = self._accumulators.get(symbol)
        completed = False
        if current is not None and minute > current.timestamp:
            self._bars.setdefault(symbol, []).append(current.completed())
            self._bars[symbol] = self._bars[symbol][-120:]
            completed = True
            current = None
        if current is None:
            current = _BarAccumulator(
                symbol, minute, event.payload.price, event.payload.price,
                event.payload.price, event.payload.price, delta,
            )
            self._accumulators[symbol] = current
        elif minute == current.timestamp:
            current.update(event.payload.price, delta)
        return completed

    def _observe_stages(self, candidate: MomentumCandidate, entry_ready: bool) -> None:
        symbol = candidate.symbol
        self._stage_symbols["discovered"].add(symbol)
        if candidate.stocks_in_play:
            self._stage_symbols["stocks_in_play"].add(symbol)
        if candidate.status is CandidateStatus.NEAR_QUALIFIED:
            self._stage_symbols["near"].add(symbol)
        if candidate.score.total >= self.strategy_config.discovery.near_qualified_score:
            self._stage_symbols["near"].add(symbol)
        if candidate.score.total >= self.strategy_config.discovery.qualified_score:
            self._stage_symbols["qualified"].add(symbol)
        if candidate.setup is not None and candidate.setup.state is SetupState.FORMING:
            self._stage_symbols["setup_forming"].add(symbol)
        if candidate.setup is not None and candidate.setup.state is SetupState.TRIGGERED:
            self._stage_symbols["triggered"].add(symbol)
            if not entry_ready:
                self._stage_symbols["blocked"].add(symbol)
        if entry_ready:
            self._stage_symbols["entry_ready"].add(symbol)

    def _focus_item(self, candidate: MomentumCandidate) -> WarriorFocusItem:
        setup = candidate.setup
        market_timestamp = self._market_data_timestamp.get(candidate.symbol)
        market_age = (
            None if market_timestamp is None
            else Decimal(str(max(
                0, (self._aware_now() - market_timestamp).total_seconds(),
            )))
        )
        return WarriorFocusItem(
            candidate, self._provenance.get(candidate.symbol, FloatProvenance.UNKNOWN),
            None if setup is None else setup.trigger,
            None if setup is None else setup.stop_price,
            self._blocking.get(candidate.symbol, ()),
            (
                market_age is None
                or market_age
                > self.capture_config.quote_stale_after_seconds
            ),
            market_age,
            decision_timestamp=candidate.timestamp,
            decision_last=candidate.price,
            decision_bid=candidate.bid,
            decision_ask=candidate.ask,
            decision_spread_percent=candidate.spread_percent,
        )

    def _restore_bars(self) -> None:
        assert self._store is not None
        records = (
            record for record, fingerprint in records_with_configuration_fingerprint(
                self._store.records()
            ) if fingerprint == self.configuration_fingerprint
            and record.record_type is CaptureRecordType.MINUTE_BAR
        )
        for record in records:
            payload = record.payload
            bar = MinuteBar(
                record.symbol, datetime.fromisoformat(payload["bar_timestamp"]),
                Decimal(payload["open"]), Decimal(payload["high"]),
                Decimal(payload["low"]), Decimal(payload["close"]),
                Decimal(payload["volume"]),
            )
            self._bars.setdefault(record.symbol, []).append(bar)
        for symbol, values in self._bars.items():
            self._bars[symbol] = sorted(values, key=lambda item: item.timestamp)[-120:]

    def _session_record(self, action: str, now: datetime) -> CaptureRecord:
        metrics = None if self._writer is None else self._writer.metrics()
        return CaptureRecord.create(
            CaptureRecordType.OBSERVATION_SESSION, STRATEGY_VERSION, now,
            {
                "action": action, "strategy_version": STRATEGY_VERSION,
                "schema_version": CAPTURE_SCHEMA_VERSION,
                "trading_date": now.astimezone(EASTERN).date(),
                "capture_start": self._started_at,
                "capture_end": now if action == "END" else None,
                "environment": self.environment,
                "configuration_fingerprint": self.configuration_fingerprint,
                "observation_run_key": self._run_key,
                "capture_metrics": None if metrics is None else {
                    "queue_depth": metrics.queue_depth,
                    "records_written": metrics.records_written,
                    "average_write_latency_ms": metrics.average_write_latency_ms,
                    "maximum_write_latency_ms": metrics.maximum_write_latency_ms,
                    "dropped_records": metrics.dropped_records,
                    "diagnostic_queue_depth": metrics.diagnostic_queue_depth,
                    "diagnostic_dropped_records": metrics.diagnostic_dropped_records,
                    "critical_failure_state": metrics.critical_failure_state,
                    "critical_failure_count": metrics.critical_failure_count,
                    "duplicate_records": metrics.duplicate_records,
                    "synchronous_fallback_records": metrics.synchronous_fallback_records,
                    "gui_refresh_frequency_hz": metrics.gui_refresh_frequency_hz,
                },
            },
            identity_parts=(action, self._run_key or "unstarted"),
        )

    def _update_health(self, *, allow_recovery: bool) -> None:
        assert self._writer is not None
        metrics = self._writer.metrics()
        # Observe this lane once per callback (before processing).  The
        # cumulative drop counter is evidence, while health reflects whether
        # loss/pressure is still occurring and can therefore recover.
        if not allow_recovery:
            new_diagnostic_loss = (
                metrics.diagnostic_dropped_records
                > self._last_diagnostic_drop_count
            )
            diagnostic_degraded = bool(
                new_diagnostic_loss or metrics.diagnostic_queue_depth > 96
            )
            self._set_observability_health(
                WarriorObservabilityHealth.DEGRADED
                if diagnostic_degraded else WarriorObservabilityHealth.RUNNING,
                reason=(
                    "DIAGNOSTIC_RECORDS_DROPPED"
                    if new_diagnostic_loss
                    else "DIAGNOSTIC_QUEUE_PRESSURE"
                    if diagnostic_degraded
                    else "DIAGNOSTIC_QUEUE_HEALTHY"
                ),
                metrics=metrics,
            )
            # A transition record is itself best-effort diagnostic traffic;
            # include any loss it encountered in the new baseline.
            self._last_diagnostic_drop_count = self._writer.metrics().diagnostic_dropped_records
        if not self._writer.healthy or metrics.critical_failure_state:
            self._accept_execution = False
            self._set_strategy_health(
                WarriorCaptureHealth.DEGRADED,
                category="CRITICAL_PERSISTENCE_FAILURE",
                reason="CAPTURE_WRITER_FAILED",
                exception=self._writer.failure,
                metrics=metrics,
            )
        elif self._health is WarriorCaptureHealth.RUNNING:
            self._accept_execution = True
        elif allow_recovery:
            self._set_strategy_health(
                WarriorCaptureHealth.RUNNING,
                category="RECOVERY", reason="HEALTH_CHECK_PASSED",
                metrics=metrics,
            )
            self._accept_execution = True

    def _set_strategy_health(
        self, health: WarriorCaptureHealth, *, category: str, reason: str,
        exception: BaseException | None = None,
        metrics: CaptureMetrics | None = None,
    ) -> None:
        previous = self._health
        self._health = health
        if previous is health:
            return
        self._record_health_transition(
            previous.value, health.value, category, reason,
            exception=exception, metrics=metrics,
        )

    def _set_observability_health(
        self, health: WarriorObservabilityHealth, *, reason: str,
        metrics: CaptureMetrics | None = None,
    ) -> None:
        previous = self._observability_health
        self._observability_health = health
        if previous is health:
            return
        self._record_health_transition(
            previous.value, health.value, "NONCRITICAL_OBSERVABILITY", reason,
            metrics=metrics,
        )

    def _record_health_transition(
        self, previous: str, new: str, category: str, reason: str, *,
        exception: BaseException | None = None,
        metrics: CaptureMetrics | None = None,
    ) -> None:
        """Publish sanitized, best-effort health evidence without authority."""
        try:
            metrics = metrics or (
                None if self._writer is None else self._writer.metrics()
            )
            transition = WarriorHealthTransition(
                # Observability must not advance or fail a strategy clock.
                timestamp=(
                    self._last_observation_at
                    or self._started_at
                    or datetime.now(UTC)
                ),
                previous_state=previous,
                new_state=new,
                category=category,
                reason=reason,
                exception_class=(
                    None if exception is None else type(exception).__name__
                ),
                diagnostic_dropped_records=(
                    0 if metrics is None else metrics.diagnostic_dropped_records
                ),
                critical_failure_state=(
                    False if metrics is None else metrics.critical_failure_state
                ),
                critical_failure_count=(
                    0 if metrics is None else metrics.critical_failure_count
                ),
                capture_queue_depth=(
                    0 if metrics is None else metrics.queue_depth
                ),
                diagnostic_queue_depth=(
                    0 if metrics is None else metrics.diagnostic_queue_depth
                ),
                last_observation_at=self._last_observation_at,
                last_full_evaluation_at=self._last_full_evaluation_at,
            )
            self._last_health_transition = transition
            _safe_warrior_observe(
                self._observability, "HEALTH_TRANSITION", "WARRIOR",
                **asdict(transition),
            )
            writer = self._writer
            if writer is not None and writer.healthy:
                writer.submit_diagnostic(CaptureRecord.create(
                    CaptureRecordType.HEALTH_TRANSITION,
                    STRATEGY_VERSION,
                    transition.timestamp,
                    asdict(transition),
                    identity_parts=(
                        transition.timestamp.isoformat(), previous, new,
                        category, reason,
                    ),
                ))
        except Exception:
            # Health observability is non-authoritative by definition.
            return

    def _flush_capture_writer(self, writer: ForwardCaptureWriter) -> None:
        started = perf_counter()
        try:
            writer.flush()
        finally:
            duration_ms = (perf_counter() - started) * 1000.0
            performance_diagnostics.record_completed_bar_flush_duration(duration_ms)
            performance_diagnostics.mark_latency_trace_stage(
                "completed_bar_flush_duration_ms", duration_ms
            )

    def _request_report_refresh(self, trading_date: date, *, persist: bool = False) -> None:
        worker = self._report_worker
        if worker is None:
            return
        started = perf_counter()
        try:
            worker.request_refresh(
                trading_date,
                configuration_fingerprint=self.configuration_fingerprint,
                persist=persist,
            )
        finally:
            duration_ms = (perf_counter() - started) * 1000.0
            performance_diagnostics.record_report_request_duration(duration_ms)
            performance_diagnostics.mark_latency_trace_stage(
                "report_refresh_request_duration_ms", duration_ms
            )

    def _accept_report(self, report: DailyForwardReport) -> None:
        self._daily_report = report
        self._report_error_type = None

    def _record_report_failure(self, error: BaseException) -> None:
        self._report_error_type = type(error).__name__
        self._last_error_type = f"REPORT:{type(error).__name__}"

    def _persist_latency_diagnostic(self, kind: str, payload: dict[str, object]) -> None:
        writer = self._writer
        if writer is None:
            return
        payload = {"diagnostic_kind": kind, **payload}
        timestamp_value = payload.get("recorded_at") or payload.get("timestamp")
        timestamp = (
            datetime.fromisoformat(str(timestamp_value))
            if timestamp_value is not None
            else self._aware_now()
        )
        symbol = str(payload.get("symbol") or "MARKET_DATA")
        record_type = (
            CaptureRecordType.CALLBACK_QUEUE_THRESHOLD
            if kind == "callback_queue_threshold"
            else CaptureRecordType.LATENCY_DIAGNOSTIC
        )
        identity = tuple(
            str(payload.get(name) or "")
            for name in ("source", "sequence", "threshold", "direction", "recorded_at")
        )
        accepted = writer.submit_diagnostic(CaptureRecord.create(
            record_type, symbol, timestamp, payload, identity_parts=identity,
        ))
        if not accepted:
            raise RuntimeError("diagnostic capture queue unavailable")

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Warrior sidecar clock must be timezone-aware")
        return value

    def _observe_decision_and_entry(self, *, value: object, candidate: object,
                                    signal: object | None = None,
                                    taxonomy_candidate: object | None = None,
                                    legacy_candidate: object | None = None,
                                    decision_timestamp: object | None = None) -> tuple[object | None, object | None]:
        """Run production DI-2 and DI-ENTRY as one owned callback boundary."""
        observer = self._decision_intelligence_observer
        policy = self._paper_entry_intelligence
        service = self._service
        symbol = str(getattr(candidate, "symbol", "UNKNOWN"))
        timestamp = decision_timestamp or getattr(candidate, "timestamp", None) or self._aware_now()
        opportunity_hint = getattr(candidate, "taxonomy_opportunity_id", None)
        self._record_di_entry_diagnostic(
            "CALLBACK_ENTERED", symbol=symbol, timestamp=timestamp,
            opportunity_id=opportunity_hint,
        )
        if observer is None or service is None:
            self._record_di_entry_diagnostic(
                "POLICY_OBJECT_MISSING", symbol=symbol, timestamp=timestamp,
                opportunity_id=opportunity_hint, reason="OTHER_GUARD",
            )
            return None, None
        if str(self.environment).upper() != "PAPER":
            self._record_di_entry_diagnostic(
                "POLICY_OBJECT_MISSING", symbol=symbol, timestamp=timestamp,
                opportunity_id=opportunity_hint, reason="UNSUPPORTED_ENVIRONMENT",
            )
            return None, None
        try:
            result = observer.observe_decision(
                value=value, candidate=candidate, signal=signal,
                taxonomy_candidate=taxonomy_candidate,
                legacy_candidate=legacy_candidate,
            )
        except Exception as exc:
            self._record_di_entry_diagnostic(
                "DI_RESULT_MISSING", symbol=symbol, timestamp=timestamp,
                opportunity_id=opportunity_hint, reason="OTHER_GUARD",
                error_type=type(exc).__name__, error_message=_safe_diagnostic_message(exc),
            )
            return None, None
        opportunity_id = opportunity_hint
        try:
            if result is None:
                self._record_di_entry_diagnostic(
                    "DI_RESULT_MISSING", symbol=symbol, timestamp=timestamp,
                    opportunity_id=opportunity_id, reason="NO_DI_RESULT",
                )
                return None, None
            opportunity_id = result.opportunity_id
            if opportunity_id is None:
                self._record_di_entry_diagnostic(
                    "OPPORTUNITY_ID_MISSING", symbol=symbol, timestamp=timestamp,
                    reason="NO_OPPORTUNITY_ID",
                )
                return result, None
            if (
                policy is None
                or not getattr(policy.config, "enabled", False)
                or getattr(policy.config, "mode", "") != "PAPER_TREATMENT"
            ):
                self._record_di_entry_diagnostic(
                    "POLICY_OBJECT_MISSING", symbol=symbol, timestamp=timestamp,
                    opportunity_id=opportunity_id, reason="DISABLED_POLICY",
                )
                return result, None
            self._record_di_entry_diagnostic(
                "POLICY_ASSESS_CALLED", symbol=symbol, timestamp=timestamp,
                opportunity_id=opportunity_id,
            )
            _decision, treatment_signal = policy.assess(
                result=result, candidate=candidate, environment="PAPER",
                signal_factory=service.runtime.entry_signal,
                decision_timestamp=decision_timestamp,
                existing_signal=signal,
            )
            self._record_di_entry_diagnostic(
                "POLICY_ASSESS_RETURNED", symbol=symbol, timestamp=timestamp,
                opportunity_id=opportunity_id,
                treatment_decision=getattr(_decision, "treatment_decision", None),
            )
            if getattr(_decision, "assignment_persisted", False):
                self._record_di_entry_diagnostic(
                    "ASSIGNMENT_PERSISTED", symbol=symbol, timestamp=timestamp,
                    opportunity_id=opportunity_id,
                )
            elif getattr(_decision, "assignment_persistence_reason", None):
                self._record_di_entry_diagnostic(
                    "ASSIGNMENT_PERSISTENCE_FAILED", symbol=symbol, timestamp=timestamp,
                    opportunity_id=opportunity_id,
                    reason=str(_decision.assignment_persistence_reason),
                )
            return result, treatment_signal
        except Exception as exc:
            self._record_di_entry_diagnostic(
                "POLICY_ASSESS_EXCEPTION", symbol=symbol, timestamp=timestamp,
                opportunity_id=opportunity_id,
                error_type=type(exc).__name__, error_message=_safe_diagnostic_message(exc),
            )
            return result, None

    def _record_di_entry_diagnostic(self, event: str, *, symbol: str,
                                    timestamp: datetime, opportunity_id: object = None,
                                    reason: str | None = None, **details: object) -> None:
        """Persist sparse DI-ENTRY seam evidence without affecting execution."""
        writer = self._writer
        if writer is None or timestamp.tzinfo is None:
            return
        identity = str(opportunity_id or symbol).strip() or "UNKNOWN"
        key = (event, identity)
        with self._lock:
            if key in self._di_entry_diagnostic_key_set:
                return
            if len(self._di_entry_diagnostic_keys) == self._di_entry_diagnostic_keys.maxlen:
                self._di_entry_diagnostic_key_set.discard(self._di_entry_diagnostic_keys[0])
            self._di_entry_diagnostic_keys.append(key)
            self._di_entry_diagnostic_key_set.add(key)
        payload = {"event": event, "opportunity_id": str(opportunity_id) if opportunity_id else None}
        if reason is not None:
            payload["reason"] = reason
        payload.update({key: value for key, value in details.items() if value is not None})
        try:
            writer.submit_diagnostic(CaptureRecord.create(
                CaptureRecordType.DI_ENTRY_DIAGNOSTIC, symbol, timestamp, payload,
                identity_parts=(event, identity),
            ))
        except Exception:
            return


def _safe_diagnostic_message(error: Exception) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ")
    message = re.sub(
        r"(?i)(token|secret|password|credential|account[_ -]?id|api[_ -]?key|access[_ -]?key)\s*[:=]\s*\S+",
        r"\1=<redacted>", message,
    )
    message = re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", message)
    return message[:256] if message else "<empty>"


class CompositeMarketEventObserver:
    """Preserve existing paper observer while adding an isolated sidecar."""

    def __init__(self, primary: Callable[[MarketEvent], object] | None,
                 warrior: WarriorDesktopSidecar,
                 research: object | None = None,
                 adaptive_entry: object | None = None,
                 *, async_projections: bool = False,
                 projection_capacity: int = 512) -> None:
        self.primary = primary
        self.warrior = warrior
        self.research = research
        self.adaptive_entry = adaptive_entry
        self.adaptive_entry_failures = 0
        self.async_projections = bool(async_projections)
        self._research_handoff = None
        self._adaptive_handoff = None
        if self.async_projections:
            self._research_handoff = BoundedProjectionHandoff(
                self._dispatch_research, maximum_keys=projection_capacity,
            )
            self._adaptive_handoff = BoundedProjectionHandoff(
                self._dispatch_adaptive, maximum_keys=projection_capacity,
            )
            setter = getattr(self.warrior, "set_research_observer", None)
            if research is not None and callable(setter):
                setter(_ResearchHandoffProxy(self._research_handoff))

    def start(self, _environment: str | None = None) -> None:
        if self._research_handoff is not None:
            self._research_handoff.start()
        if self._adaptive_handoff is not None:
            self._adaptive_handoff.start()
        research_start = getattr(self.research, "start", None)
        if callable(research_start):
            research_start(_environment)
        adaptive_start = getattr(self.adaptive_entry, "start", None)
        if callable(adaptive_start):
            adaptive_start(_environment)
        self.warrior.start(_environment)

    def stop(self) -> None:
        if self._research_handoff is not None:
            self._research_handoff.stop(drain=False)
        if self._adaptive_handoff is not None:
            self._adaptive_handoff.stop(drain=False)
        research_stop = getattr(self.research, "stop", None)
        if callable(research_stop):
            try:
                research_stop()
            except Exception:
                pass
        adaptive_stop = getattr(self.adaptive_entry, "stop", None)
        if callable(adaptive_stop):
            try:
                adaptive_stop()
            except Exception:
                pass
        self.warrior.stop()

    def session_policy_tick(self, observed_at: datetime | None = None) -> None:
        self.warrior.session_policy_tick(observed_at)

    def overnight_capability_lost(self, observed_at: datetime | None = None) -> None:
        self.warrior.overnight_capability_lost(observed_at)

    def projection_metrics(self) -> dict[str, dict[str, int | float]]:
        return {
            "research": {} if self._research_handoff is None else self._research_handoff.memory_metrics(),
            "adaptive": {} if self._adaptive_handoff is None else self._adaptive_handoff.memory_metrics(),
        }

    def _dispatch_research(self, value: object) -> None:
        if self.research is None:
            return
        kind, payload = value
        if kind == "event":
            self.research(payload)
        elif kind == "decision":
            callback = getattr(self.research, "observe_scanner_decision", None)
            if callable(callback):
                callback(payload)
        else:
            point, candidate, signal = payload
            callback = getattr(self.research, "observe_warrior_decision", None)
            if callable(callback):
                callback(point, candidate, signal)

    def _dispatch_adaptive(self, event: object) -> None:
        if not callable(self.adaptive_entry):
            return
        try:
            self.adaptive_entry(event)
        except Exception:
            self.adaptive_entry_failures += 1

    def __call__(self, event: MarketEvent) -> None:
        if self.primary is not None:
            self._timed("paper.market_event", self.primary, event)
        self._timed("warrior.desktop_sidecar", self.warrior, event)
        if self._research_handoff is not None:
            self._research_handoff.submit(
                _projection_key(event), ("event", event),
            )
        elif callable(self.research):
            self._timed("research.trade_intelligence", self.research, event)
        if self._adaptive_handoff is not None:
            self._adaptive_handoff.submit(_projection_key(event), event)
        elif callable(self.adaptive_entry):
            try:
                self._timed("research.adaptive_entry", self.adaptive_entry, event)
            except Exception:
                # Defense in depth: adaptive research runs last and can never
                # unwind the authoritative PAPER/Warrior event pipeline.
                self.adaptive_entry_failures += 1

    @staticmethod
    def _timed(component: str, observer: Callable[[MarketEvent], object], event: MarketEvent) -> object:
        started = perf_counter()
        success = False
        try:
            result = observer(event)
            success = True
            return result
        finally:
            performance_diagnostics.record_component_duration(
                component,
                (perf_counter() - started) * 1000.0,
                event_type=getattr(getattr(event, "event_type", None), "value", None),
                symbol=getattr(event, "symbol", None),
                success=success,
            )

    def observe_scanner_decision(self, decision: object) -> None:
        observer = getattr(self.research, "observe_scanner_decision", None)
        if callable(observer):
            if self._research_handoff is not None:
                self._research_handoff.submit(
                    _projection_key(decision), ("decision", decision),
                )
            else:
                observer(decision)

    def reset_symbol(self, symbol: str) -> None:
        observer = getattr(self.research, "reset_symbol", None)
        if callable(observer):
            observer(symbol)

    def bind_scanner_adapter(self, adapter: MarketEventScannerAdapter) -> None:
        self.warrior.bind_scanner_adapter(adapter)

    def bind_scanner_decision_source(
        self, source: Callable[[str], object | None],
        ranked_source: Callable[[str], bool] | None = None,
    ) -> None:
        self.warrior.bind_scanner_decision_source(source, ranked_source)

    def needs_historical_preload(self, symbol: str) -> bool:
        return self.warrior.needs_historical_preload(symbol)

    def preload_historical_bars(
        self,
        symbol: str,
        bars: Iterable[object],
    ) -> int:
        return self.warrior.preload_historical_bars(symbol, bars)

    def retained_symbols(self) -> tuple[str, ...]:
        values = set(self.warrior.retained_symbols())
        research_values = getattr(self.research, "retained_symbols", None)
        if callable(research_values):
            values.update(research_values())
        return tuple(sorted(values))


class _ResearchHandoffProxy:
    """Advisory TI facade used by the authoritative Warrior sidecar."""

    def __init__(self, handoff: BoundedProjectionHandoff) -> None:
        self._handoff = handoff

    def __call__(self, event: object) -> None:
        self._handoff.submit(_projection_key(event), ("event", event))

    def observe_warrior_decision(
        self, point: object, candidate: object, signal: object,
    ) -> None:
        self._handoff.submit(
            _projection_key(candidate), ("warrior", (point, candidate, signal)),
        )


def _projection_key(value: object) -> str:
    symbol = getattr(value, "symbol", None)
    if symbol is None and isinstance(value, tuple) and value:
        symbol = getattr(value[0], "symbol", None)
    return str(symbol or "__GLOBAL__").strip().upper()


def _blocking_reasons(candidate: MomentumCandidate, entry_ready: bool) -> tuple[str, ...]:
    if entry_ready:
        return ()
    mapping = {
        "PRICE_TOO_LOW": "price", "PRICE_TOO_HIGH": "price",
        "CHANGE_TOO_LOW": "change", "RVOL_LOW": "scanner_rvol",
        "FLOAT_HIGH": "float", "SPREAD_WIDE": "spread",
        "LIQUIDITY_LOW": "participation",
        "HALTED": "halt", "HALT_UNKNOWN": "halt",
        "NOT_TRADABLE": "tradability", "SESSION_NOT_ALLOWED": "session",
        "STOP_TOO_WIDE": "risk", "STOP_INVALID": "risk",
        "RISK_REJECTED": "strategy_eligibility", "NO_SETUP": "setup",
        "STALE_MARKET_DATA": "stale_market_data",
        "AWAITING_EXECUTION_QUOTE": "awaiting_execution_quote",
    }
    return tuple(dict.fromkeys(
        mapping[code.value] for code in candidate.reason_codes if code.value in mapping
    ))


def _scanner_classification(decision: object, ranked: bool) -> str | None:
    """Mirror the existing scanner projection labels for captured context."""
    if ranked:
        return "QUALIFYING"
    if bool(getattr(decision, "technical_qualifies_without_catalyst", False)):
        return "WATCHING"
    failed = tuple(getattr(decision, "technical_failed_rules", ()))
    if len(failed) == 1 and failed[0] in {
        "price_range", "percentage_change", "relative_volume", "low_float",
        "dollar_volume", "spread",
    }:
        return "NEAR MISS"
    return None


__all__ = [
    "CompositeMarketEventObserver", "STRATEGY_VERSION", "WarriorCaptureHealth",
    "WarriorDesktopSidecar", "WarriorFocusItem", "WarriorHealthTransition",
    "WarriorObservabilityHealth", "WarriorPaperSnapshot", "WarriorPaperSummary",
    "strategy_configuration_fingerprint",
]
