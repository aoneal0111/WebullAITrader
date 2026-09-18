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
from .market_event_observer import CompositeMarketEventObserver
from .capture_io import flush_capture_writer, request_report_refresh
from .capture_support import (
    build_latency_diagnostic_record,
    build_session_record,
    safe_diagnostic_message,
)
from .projection_models import (
    WarriorFocusItem,
    WarriorPaperSnapshot,
    WarriorPaperSummary,
    blocking_reasons,
    scanner_classification,
)
from .models import CandidateStatus, MinuteBar, MomentumCandidate, SetupState
from .observability import NoOpWarriorObservabilitySink
from .runtime import WarriorMomentumRuntime
from .shadow_latched import (
    ShadowLatchedTransition,
    ShadowMarketObservation,
)


_RUNTIME_LOGGER = logging.getLogger("atlas.runtime")

STRATEGY_VERSION = "WARRIOR_MOMENTUM_V1"
_PROTECTION_AUDIT_INTERVAL_SECONDS = 15.0


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
    material = canonical_json({
        "strategy_version": STRATEGY_VERSION,
        "configuration": asdict(config),
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
        self._last_error_type: str | None = None
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

    def bind_scanner_adapter(self, adapter: MarketEventScannerAdapter) -> None:
        if not isinstance(adapter, MarketEventScannerAdapter):
            raise TypeError("Warrior sidecar requires the shared scanner adapter")
        with self._lock:
            self._adapter = adapter

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
                self._health = WarriorCaptureHealth.RUNNING
                self._accept_execution = True
                self._order_flow.start()
            except Exception as exc:
                self._last_error_type = type(exc).__name__
                self._health = WarriorCaptureHealth.DEGRADED
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
            if self._health is not WarriorCaptureHealth.RUNNING:
                return
            try:
                self._consume(event)
                if event.symbol is not None and self._service is not None:
                    normalized = event.symbol.strip().upper()
                    if self._protection_reconciliation_due(normalized):
                        reconcile_service = self._service
                        reconcile_symbol = normalized
                self._update_health()
            except Exception as exc:
                # Capture health is deliberately isolated from stream health.
                self._last_error_type = type(exc).__name__
                self._health = WarriorCaptureHealth.DEGRADED
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
                self._health = WarriorCaptureHealth.DEGRADED
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
            return
        completed = False
        if (
            event.event_type is MarketEventType.TRADE
            and isinstance(event.payload, TradePayload)
            and event.payload.volume_semantics is VolumeSemantics.TRADE_SIZE
            and not event.payload.trade_id.startswith("snapshot")
        ):
            aggregate_started = perf_counter()
            completed = self._aggregate_trade(event, observation.current_volume)
            performance_diagnostics.record_component_duration(
                "warrior.trade_aggregation",
                (perf_counter() - aggregate_started) * 1000.0,
                event_type="TRADE",
                symbol=symbol,
            )
        if completed:
            service.invalidate_intraminute_shadow(
                symbol,
                observation.timestamp,
                ShadowLatchedTransition.NEW_BAR_INVALIDATION,
                reason="NEW_COMPLETED_BAR",
                processing_time=self._aware_now(),
            )
        if symbol not in self._first_observed or completed:
            available_bars = tuple(self._bars.get(symbol, ())[-120:])
            history = canonical_completed_history(
                available_bars, observation.timestamp,
                session=scanner_session(observation.timestamp).value,
            )
            # Historical preload is useful only when the live bar stream has
            # reached the same point in time.  A stale preload must not make a
            # current decision appear to have fresh setup history.
            if not current_completed_bar_tail(available_bars, observation.timestamp):
                history = ()
            quote_freshness = last_price_freshness = None
            state = adapter.state_for(symbol)
            evaluated_at = self._aware_now()
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
                scanner_classification = scanner_classification(
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
                    observation, scanner_session(observation.timestamp).value,
                    history, float_provenance=provenance,
                    catalyst_source="WEBULL_EARNINGS_SEC",
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
                self._blocking[symbol] = blocking_reasons(candidate, signal is not None)
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
                session=scanner_session(observation.timestamp).value,
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
        return build_session_record(
            writer=self._writer,
            strategy_version=STRATEGY_VERSION,
            action=action,
            now=now,
            started_at=self._started_at,
            environment=self.environment,
            configuration_fingerprint=self.configuration_fingerprint,
            run_key=self._run_key,
        )

    def _update_health(self) -> None:
        assert self._writer is not None
        metrics = self._writer.metrics()
        if not self._writer.healthy or metrics.dropped_records:
            self._health = WarriorCaptureHealth.DEGRADED
        elif metrics.queue_depth > self.capture_config.queue_capacity * 3 // 4:
            self._health = WarriorCaptureHealth.DEGRADED

    def _flush_capture_writer(self, writer: ForwardCaptureWriter) -> None:
        flush_capture_writer(writer)

    def _request_report_refresh(
        self,
        trading_date: date,
        *,
        persist: bool = False,
    ) -> None:
        request_report_refresh(
            self._report_worker,
            trading_date=trading_date,
            configuration_fingerprint=self.configuration_fingerprint,
            persist=persist,
        )

    def _accept_report(self, report: DailyForwardReport) -> None:
        self._daily_report = report
        self._report_error_type = None

    def _record_report_failure(self, error: BaseException) -> None:
        self._report_error_type = type(error).__name__
        self._last_error_type = f"REPORT:{type(error).__name__}"

    def _persist_latency_diagnostic(
        self,
        kind: str,
        payload: dict[str, object],
    ) -> None:
        writer = self._writer
        if writer is None:
            return
        record = build_latency_diagnostic_record(
            kind=kind,
            payload=payload,
            fallback_timestamp=self._aware_now(),
        )
        if not writer.submit_diagnostic(record):
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
                error_type=type(exc).__name__, error_message=safe_diagnostic_message(exc),
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
                error_type=type(exc).__name__, error_message=safe_diagnostic_message(exc),
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


def safe_diagnostic_message(error: Exception) -> str:
    message = str(error).replace("\r", " ").replace("\n", " ")
    message = re.sub(
        r"(?i)(token|secret|password|credential|account[_ -]?id|api[_ -]?key|access[_ -]?key)\s*[:=]\s*\S+",
        r"\1=<redacted>", message,
    )
    message = re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", message)
    return message[:256] if message else "<empty>"


def blocking_reasons(candidate: MomentumCandidate, entry_ready: bool) -> tuple[str, ...]:
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


def scanner_classification(decision: object, ranked: bool) -> str | None:
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
    "WarriorDesktopSidecar", "WarriorFocusItem", "WarriorPaperSnapshot",
    "WarriorPaperSummary", "strategy_configuration_fingerprint",
]
