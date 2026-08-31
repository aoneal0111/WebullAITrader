"""Desktop-owned, stream-sharing Warrior V1 forward-paper observer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import logging
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Callable, Iterable

from app.live_scanner.session import scanner_session
from app.market.calendar import EASTERN
from app.market_data.models import MarketEvent, MarketEventType, TradePayload
from app.scanner_adapter.adapter import MarketEventScannerAdapter
from app.services.runtime_diagnostics import log_runtime_exception

from .configuration import WarriorMomentumConfig
from .features import contiguous_tail, current_completed_bar_tail
from .forward_models import (
    CAPTURE_SCHEMA_VERSION, CaptureMetrics, CaptureRecord, CaptureRecordType,
    FloatProvenance, ForwardCaptureConfiguration, PaperAccountContext,
    PointInTimeObservation, canonical_json,
)
from .forward_queue import ForwardCaptureWriter
from .forward_report import DailyForwardReport, build_daily_report, persist_daily_report
from .forward_runtime import WarriorForwardCaptureService
from .execution_quote import ExecutionQuoteSource
from .forward_store import ForwardCaptureStore
from .models import CandidateStatus, MinuteBar, MomentumCandidate, SetupState
from .runtime import WarriorMomentumRuntime
from .shadow_latched import (
    ShadowLatchedTransition,
    ShadowMarketObservation,
)


_RUNTIME_LOGGER = logging.getLogger("atlas.runtime")

STRATEGY_VERSION = "WARRIOR_MOMENTUM_V1"


class WarriorCaptureHealth(StrEnum):
    DISABLED = "DISABLED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPED = "STOPPED"


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
        paper_exit_submitter: Callable[[str, int, Decimal, str, str | None], bool] | None = None,
        paper_position_quantity_source: Callable[[str], Decimal] | None = None,
        execution_quote_source: ExecutionQuoteSource | None = None,
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
        self._paper_position_quantity_source = paper_position_quantity_source
        self._execution_quote_source = execution_quote_source
        self._accept_execution = False
        self._clock = clock
        self._lock = RLock()
        self._adapter: MarketEventScannerAdapter | None = None
        self._scanner_decision_source: Callable[[str], object | None] | None = None
        self._scanner_ranked_source: Callable[[str], bool] | None = None
        self._store: ForwardCaptureStore | None = None
        self._writer: ForwardCaptureWriter | None = None
        self._service: WarriorForwardCaptureService | None = None
        self._health = WarriorCaptureHealth.DISABLED if not enabled else WarriorCaptureHealth.STOPPED
        self._last_error_type: str | None = None
        self._bars: dict[str, list[MinuteBar]] = {}
        self._accumulators: dict[str, _BarAccumulator] = {}
        self._last_volume: dict[str, Decimal] = {}
        self._latest: dict[str, MomentumCandidate] = {}
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
                self._store = ForwardCaptureStore(self.storage_path)
                self._writer = ForwardCaptureWriter(
                    self._store, capacity=self.capture_config.queue_capacity,
                    batch_size=self.capture_config.batch_size,
                    flush_interval_seconds=self.capture_config.flush_interval_seconds,
                )
                self._service = WarriorForwardCaptureService(
                    self._store, self._writer, self.strategy_config,
                    self.capture_config,
                    paper_entry_submitter=self._paper_entry_submitter,
                    paper_exit_submitter=self._paper_exit_submitter,
                    paper_position_quantity_source=self._paper_position_quantity_source,
                    execution_quote_source=self._execution_quote_source,
                    execution_permitted=lambda: self._accept_execution,
                    account_refresh_source=self._account_source,
                )
                self._restore_bars()
                now = self._aware_now()
                self._started_at = now
                self._run_key = sha256(
                    f"{self.configuration_fingerprint}|{self.environment}|{now.isoformat()}".encode()
                ).hexdigest()
                self._writer.submit(self._session_record("START", now))
                self._daily_report = build_daily_report(
                    self._store, now.astimezone(EASTERN).date(),
                    configuration_fingerprint=self.configuration_fingerprint,
                )
                self._health = WarriorCaptureHealth.RUNNING
                self._accept_execution = True
            except Exception as exc:
                self._last_error_type = type(exc).__name__
                self._health = WarriorCaptureHealth.DEGRADED

    def stop(self) -> None:
        if not self.enabled:
            return
        # Publish shutdown intent before waiting for an in-flight confirmation.
        self._accept_execution = False
        with self._lock:
            writer, store = self._writer, self._store
            if writer is None or store is None:
                self._health = WarriorCaptureHealth.STOPPED
                return
            lifecycle_phase = "shadow outcome finalization"
            try:
                now = self._aware_now()
                if self._service is not None:
                    self._service.shutdown_intraminute_shadow(now)
                    self._service.finalize_shadow_outcomes(now)
                lifecycle_phase = "shadow/capture writer drain"
                writer.flush()
                lifecycle_phase = "shadow daily report finalization"
                report = build_daily_report(
                    store, now.astimezone(EASTERN).date(),
                    configuration_fingerprint=self.configuration_fingerprint,
                )
                persist_daily_report(store, report)
                lifecycle_phase = "shadow session finalization"
                writer.submit(self._session_record("END", now))
                lifecycle_phase = "shadow/capture writer close"
                writer.close()
                lifecycle_phase = "Warrior runtime finalization"
                self._last_metrics = writer.metrics()
                self._daily_report = report
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
                self._writer = None
                self._service = None

    def __call__(self, event: MarketEvent) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._health is not WarriorCaptureHealth.RUNNING:
                return
            try:
                self._consume(event)
                self._update_health()
            except Exception as exc:
                # Capture health is deliberately isolated from stream health.
                self._last_error_type = type(exc).__name__
                self._health = WarriorCaptureHealth.DEGRADED

    def snapshot(self) -> WarriorPaperSnapshot:
        with self._lock:
            writer = self._writer
            metrics = self._last_metrics if writer is None else writer.metrics()
            if metrics is not None:
                self._last_metrics = metrics
            ranked = tuple(
                (
                    self._service.runtime
                    if self._service is not None
                    else WarriorMomentumRuntime(self.strategy_config)
                ).rank(tuple(self._latest.values()))
            )
            report = self._daily_report
            summary = WarriorPaperSummary(
                discovered=len(self._stage_symbols["discovered"]),
                stocks_in_play=len(self._stage_symbols["stocks_in_play"]),
                near=len(self._stage_symbols["near"]),
                qualified=len(self._stage_symbols["qualified"]),
                setup_forming=len(self._stage_symbols["setup_forming"]),
                triggered=len(self._stage_symbols["triggered"]),
                entry_ready=len(self._stage_symbols["entry_ready"]),
                open_paper_trades=(
                    (0 if report is None else report.open_paper_positions)
                    if self._service is None else len(self._service.open_paper_symbols)
                ),
                today_paper_r=None if report is None else report.total_r,
                today_trades=0 if report is None else report.paper_trades,
                triggered_but_blocked=len(self._stage_symbols["blocked"]),
                tracked_counterfactuals=(
                    0 if self._service is None else len(self._service.counterfactual_symbols)
                ),
            )
            elapsed = max(monotonic() - self._publication_started, 1e-9)
            return WarriorPaperSnapshot(
                self.enabled, self._health, self.configuration_fingerprint,
                tuple(self._focus_item(item) for item in ranked), summary,
                metrics, self._last_error_type,
                Decimal(str(self._publications / elapsed)),
            )

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
        observation = adapter.observation_for(symbol)
        if observation is None:
            return
        completed = False
        if event.event_type is MarketEventType.TRADE and isinstance(event.payload, TradePayload):
            completed = self._aggregate_trade(event, observation.current_volume)
        if completed:
            service.invalidate_intraminute_shadow(
                symbol,
                observation.timestamp,
                ShadowLatchedTransition.NEW_BAR_INVALIDATION,
                reason="NEW_COMPLETED_BAR",
                processing_time=self._aware_now(),
            )
        if symbol not in self._first_observed or completed:
            history = current_completed_bar_tail(
                tuple(self._bars.get(symbol, ())[-120:]),
                observation.timestamp,
            )
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
                scanner_classification = _scanner_classification(
                    scanner_decision,
                    False if self._scanner_ranked_source is None
                    else self._scanner_ranked_source(symbol),
                )
            candidate, signal = service.observe(
                PointInTimeObservation(
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
                ),
                account=self._account_source(),
            )
            self._latest[symbol] = candidate
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
            self._first_observed.add(symbol)
            self._publications += 1
            if signal is not None or completed:
                assert self._store is not None
                assert self._writer is not None
                self._writer.flush()
                self._daily_report = build_daily_report(
                    self._store, observation.timestamp.astimezone(EASTERN).date(),
                    configuration_fingerprint=self.configuration_fingerprint,
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
        for record in self._store.records(record_type=CaptureRecordType.MINUTE_BAR):
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
                    "duplicate_records": metrics.duplicate_records,
                    "synchronous_fallback_records": metrics.synchronous_fallback_records,
                    "gui_refresh_frequency_hz": metrics.gui_refresh_frequency_hz,
                },
            },
            identity_parts=(action, self._run_key or "unstarted"),
        )

    def _update_health(self) -> None:
        assert self._writer is not None
        metrics = self._writer.metrics()
        if not self._writer.healthy or metrics.dropped_records:
            self._health = WarriorCaptureHealth.DEGRADED
        elif metrics.queue_depth > self.capture_config.queue_capacity * 3 // 4:
            self._health = WarriorCaptureHealth.DEGRADED

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Warrior sidecar clock must be timezone-aware")
        return value


class CompositeMarketEventObserver:
    """Preserve existing paper observer while adding an isolated sidecar."""

    def __init__(self, primary: Callable[[MarketEvent], object] | None,
                 warrior: WarriorDesktopSidecar) -> None:
        self.primary = primary
        self.warrior = warrior

    def __call__(self, event: MarketEvent) -> None:
        if self.primary is not None:
            self.primary(event)
        self.warrior(event)

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

    def start(self, environment: str | None = None) -> None:
        self.warrior.start(environment)

    def stop(self) -> None:
        self.warrior.stop()

    def retained_symbols(self) -> tuple[str, ...]:
        return self.warrior.retained_symbols()


def _blocking_reasons(candidate: MomentumCandidate, entry_ready: bool) -> tuple[str, ...]:
    if entry_ready:
        return ()
    mapping = {
        "PRICE_TOO_LOW": "price", "PRICE_TOO_HIGH": "price",
        "CHANGE_TOO_LOW": "change", "RVOL_LOW": "rvol",
        "FLOAT_HIGH": "float", "SPREAD_WIDE": "spread",
        "LIQUIDITY_LOW": "liquidity",
        "HALTED": "halt", "HALT_UNKNOWN": "halt",
        "NOT_TRADABLE": "tradability", "SESSION_NOT_ALLOWED": "session",
        "STOP_TOO_WIDE": "risk", "STOP_INVALID": "risk",
        "RISK_REJECTED": "score/risk", "NO_SETUP": "setup",
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
    "WarriorDesktopSidecar", "WarriorFocusItem", "WarriorPaperSnapshot",
    "WarriorPaperSummary", "strategy_configuration_fingerprint",
]
