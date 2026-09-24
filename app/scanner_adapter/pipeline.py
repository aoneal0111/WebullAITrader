from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
import logging
from time import perf_counter

from app.market_data.models import MarketEvent
from app.momentum_scanner.models import ScannerDecision
from app.momentum_scanner.ranking import rank_candidates
from app.momentum_scanner.rules import (
    MomentumScannerConfig,
    evaluate_candidate,
)
from app.scanner_adapter.adapter import MarketEventScannerAdapter
from app.scanner_adapter.evaluation_mailbox import LatestEvaluationMailbox
from app.scanner_adapter.models import AdapterResult, QualificationDiagnostics
from app.performance_diagnostics import performance_diagnostics


_QUALIFICATION_RULES = (
    "price_range",
    "percentage_change",
    "relative_volume",
    "float_verified",
    "low_float",
    "news_catalyst",
    "tradable",
    "not_halted",
    "dollar_volume",
    "spread",
)

_LOGGER = logging.getLogger("atlas.scanner")
_PROCESSING_AGE_WARNING_SECONDS = 5.0
DEFAULT_CANDIDATE_RETENTION_SECONDS = 120.0


class MomentumScannerPipeline:
    def __init__(
        self,
        adapter: MarketEventScannerAdapter,
        config: MomentumScannerConfig = MomentumScannerConfig(),
        *,
        decision_sink: Callable[[ScannerDecision], object] | None = None,
        clock: Callable[[], datetime] | None = None,
        candidate_retention_seconds: float = DEFAULT_CANDIDATE_RETENTION_SECONDS,
        coalesce_observational: bool = False,
        evaluation_mailbox_capacity: int = 256,
    ) -> None:
        if candidate_retention_seconds <= 0:
            raise ValueError("candidate retention seconds must be positive")
        self.adapter = adapter
        self.config = config
        self._latest: dict[str, ScannerDecision] = {}
        self._clock = clock or (lambda: datetime.now(UTC))
        self._candidate_retention_seconds = float(candidate_retention_seconds)
        self._coalesce_observational = bool(coalesce_observational)
        self._evaluation_mailbox = LatestEvaluationMailbox(
            capacity=evaluation_mailbox_capacity,
        ) if self._coalesce_observational else None
        self._symbol_versions: dict[str, int] = {}
        # The latest accepted event is retained only as a bounded evaluation
        # cursor.  It lets a reference-warmup completion schedule evaluation
        # when live state arrived first, without replaying or fabricating a
        # market event.
        self._latest_events: dict[str, MarketEvent] = {}
        self._raw_callbacks_received = 0
        self._superseded_evaluations_skipped = 0
        self._result_version_rejections = 0
        if decision_sink is not None and not callable(decision_sink):
            raise TypeError("decision_sink must be callable or None")
        self._decision_sink = decision_sink
        self._processing_delay_count = 0
        self._processing_delay_active = False

    def consume(
        self,
        event: MarketEvent,
    ) -> ScannerDecision | None:
        result = self.adapter.consume(event)
        self._latest_events[event.symbol.strip().upper()] = event
        self._raw_callbacks_received += 1
        reduced_at = self._clock()
        performance_diagnostics.mark_latency_trace_timestamp(
            "canonical_reduction_completed_at", reduced_at,
        )
        self._record_stage_age(
            "dequeue_to_canonical_reduction", event.dequeued_timestamp,
            reduced_at,
        )
        if result is None or result.observation is None:
            return None
        if self._evaluation_mailbox is not None:
            symbol = result.state.symbol
            version = self._symbol_versions.get(symbol, 0) + 1
            self._symbol_versions[symbol] = version
            admitted_at = self._clock()
            self._evaluation_mailbox.enqueue(symbol, version, admitted_at, event)
            performance_diagnostics.mark_latency_trace_timestamp(
                "evaluation_mailbox_admitted_at", admitted_at,
            )
            self._record_stage_age(
                "reduction_to_evaluation_admission", reduced_at, admitted_at,
            )
            return None
        return self._evaluate_result(event, result)

    def reduce_transport_only(self, event: MarketEvent) -> bool:
        """Reduce canonical state without admitting scanner evaluation."""
        result = self.adapter.consume(event)
        self._latest_events[event.symbol.strip().upper()] = event
        self._raw_callbacks_received += 1
        reduced_at = self._clock()
        performance_diagnostics.mark_latency_trace_timestamp(
            "canonical_reduction_completed_at", reduced_at,
        )
        self._record_stage_age(
            "dequeue_to_canonical_reduction", event.dequeued_timestamp,
            reduced_at,
        )
        return result is not None and result.observation is not None

    def reference_ready(self, symbol: str) -> bool:
        """Schedule the latest coherent state after reference completion.

        Reference warmup runs outside the market-event consumer.  When live
        state arrived first, waiting for another tick would leave a complete
        symbol permanently absent from the asynchronous evaluation mailbox.
        This method only schedules existing canonical state; it does not
        bypass observation, qualification, freshness, or execution gates.
        """
        mailbox = self._evaluation_mailbox
        normalized = symbol.strip().upper()
        event = self._latest_events.get(normalized)
        if mailbox is None or event is None:
            return False
        refresh_reference = getattr(self.adapter, "reference_updated", None)
        if callable(refresh_reference):
            result = refresh_reference(normalized)
            observation_ready = result is not None and result.observation is not None
        else:
            observation_ready = self.adapter.observation_for(normalized) is not None
        if not observation_ready:
            return False
        version = self._symbol_versions.get(normalized, 0) + 1
        self._symbol_versions[normalized] = version
        return mailbox.enqueue(normalized, version, self._clock(), event)

    def _evaluate_result(
        self,
        event: MarketEvent,
        result,
        *,
        publish: bool = True,
    ) -> ScannerDecision | None:
        scanner_started = perf_counter()
        scanner_started_at = self._clock()
        performance_diagnostics.mark_latency_trace_timestamp(
            "scanner_evaluation_started_at", scanner_started_at,
        )
        decision = evaluate_candidate(
            result.observation,
            self.config,
        )
        observed_at = self._clock()
        self._prune_stale(observed_at)
        decision = replace(
            decision,
            observed_at=observed_at,
            source_event_identity=(
                f"{event.source.strip()}:{event.sequence}:{event.event_type.value}"
            ),
            source_event_type=event.event_type.value,
        )
        if event.received_timestamp is not None:
            age_seconds = max(
                0.0,
                (observed_at - event.received_timestamp).total_seconds(),
            )
            performance_diagnostics.record_event_processing_age(
                age_seconds * 1000.0
            )
            if age_seconds > _PROCESSING_AGE_WARNING_SECONDS:
                delay_episode_started = not self._processing_delay_active
                self._processing_delay_count += 1
                self._processing_delay_active = True
                if (
                    delay_episode_started
                    or self._processing_delay_count % 1_000 == 0
                ):
                    _LOGGER.warning(
                        "event_type=market_event_processing_delayed "
                        "delayed_event_count=%d source=%s sequence=%s "
                        "market_event_type=%s symbol=%s event_time=%s "
                        "callback_received_at=%s scanner_started_at=%s "
                        "processing_age_seconds=%.6f",
                        self._processing_delay_count,
                        event.source,
                        event.sequence,
                        event.event_type.value,
                        event.symbol,
                        event.timestamp.isoformat(),
                        event.received_timestamp.isoformat(),
                        observed_at.isoformat(),
                        age_seconds,
                    )
            elif self._processing_delay_active:
                _LOGGER.info(
                    "event_type=market_event_processing_recovered "
                    "delayed_event_count=%d processing_age_seconds=%.6f",
                    self._processing_delay_count,
                    age_seconds,
                )
                self._processing_delay_active = False
        ranked_all = sorted(
            (
                *(item for item in self._latest.values() if item.symbol != decision.symbol),
                decision,
            ),
            key=lambda item: (
                -item.score,
                -item.metrics.relative_volume,
                -item.metrics.percentage_change,
                item.symbol,
            ),
        )
        decision = replace(
            decision,
            scanner_rank=next(
                index
                for index, item in enumerate(ranked_all, 1)
                if item.symbol == decision.symbol
            ),
        )
        performance_diagnostics.record_scanner_duration(
            (perf_counter() - scanner_started) * 1000.0
        )
        performance_diagnostics.mark_latency_trace_timestamp(
            "scanner_evaluation_completed_at", observed_at,
        )
        if publish:
            self._publish_decision(decision)
        return decision

    def _publish_decision(self, decision: ScannerDecision) -> None:
        self._latest[decision.symbol] = decision
        if self._decision_sink is not None:
            capture_started = perf_counter()
            performance_diagnostics.mark_latency_trace_timestamp(
                "experiment_enqueue_started_at", datetime.now(UTC)
            )
            try:
                self._decision_sink(decision)
            finally:
                performance_diagnostics.mark_latency_trace_timestamp(
                    "experiment_enqueue_ended_at", datetime.now(UTC)
                )
                performance_diagnostics.record_experiment_capture_duration(
                    (perf_counter() - capture_started) * 1000.0
                )


    def drain_evaluations(
        self,
        *,
        maximum: int | None = None,
    ) -> tuple[ScannerDecision, ...]:
        """Evaluate current canonical state in fair symbol order."""
        mailbox = self._evaluation_mailbox
        if mailbox is None:
            return ()
        if maximum is not None and maximum <= 0:
            raise ValueError("maximum evaluations must be positive")
        decisions: list[ScannerDecision] = []
        evaluated = 0
        while maximum is None or evaluated < maximum:
            work = mailbox.pop()
            if work is None:
                break
            current_version = self._symbol_versions.get(work.symbol, 0)
            if work.version != current_version:
                self._superseded_evaluations_skipped += 1
                continue
            # Retrieve the already-reduced immutable state and rebuild its
            # observation without applying the event a second time.
            state = self.adapter.state_for(work.symbol)
            if state is None:
                self._superseded_evaluations_skipped += 1
                continue
            observation, missing = self.adapter.observation_for(work.symbol), ()
            if observation is None:
                self._superseded_evaluations_skipped += 1
                continue
            result = AdapterResult(
                state=state, observation=observation, missing_fields=missing,
            )
            evaluation_started_at = self._clock()
            self._record_stage_age(
                "evaluation_mailbox_residence", work.enqueued_at,
                evaluation_started_at,
            )
            performance_diagnostics.mark_latency_trace_timestamp(
                "evaluation_started_at", evaluation_started_at,
            )
            decision = self._evaluate_result(work.event, result, publish=False)
            evaluated += 1
            if self._symbol_versions.get(work.symbol, 0) != work.version:
                self._result_version_rejections += 1
                continue
            if decision is not None:
                self._publish_decision(decision)
                decisions.append(decision)
        return tuple(decisions)

    def consume_many(
        self,
        events: Iterable[MarketEvent],
    ) -> tuple[ScannerDecision, ...]:
        decisions: list[ScannerDecision] = []

        for event in events:
            decision = self.consume(event)
            if decision is not None:
                decisions.append(decision)

        decisions.extend(self.drain_evaluations())
        return tuple(decisions)

    def reset_symbol(self, symbol: str) -> None:
        """Discard stream-derived state for one symbol."""
        normalized = symbol.strip().upper()
        self.adapter.reset_symbol(normalized)
        self._latest_events.pop(normalized, None)
        self._latest.pop(normalized, None)
        self._symbol_versions.pop(normalized, None)
        reset = getattr(self._decision_sink, "reset_symbol", None)
        if callable(reset):
            reset(normalized)

    def latest_decision(
        self,
        symbol: str,
    ) -> ScannerDecision | None:
        self._prune_stale(self._clock())
        return self._latest.get(symbol.strip().upper())

    def memory_metrics(self) -> dict[str, object]:
        adapter_metrics = self.adapter.memory_metrics()
        metrics = {
            "raw_callbacks_received": self._raw_callbacks_received,
            "latest_decision_count": len(self._latest),
            "processing_delay_count": self._processing_delay_count,
            "superseded_evaluations_skipped": self._superseded_evaluations_skipped,
            "result_version_rejections": self._result_version_rejections,
            **adapter_metrics,
        }
        component_timings = performance_diagnostics.snapshot().component_timings
        for name in (
            "dequeue_to_canonical_reduction",
            "reduction_to_evaluation_admission",
            "evaluation_mailbox_residence",
            "callback_to_warrior_observation",
        ):
            timing = component_timings.get(name)
            if timing is not None:
                metrics[f"{name}_p99_ms"] = timing["p99_ms"]
        if self._evaluation_mailbox is not None:
            metrics.update(self._evaluation_mailbox.metrics(now=self._clock()))
        return metrics

    @staticmethod
    def _record_stage_age(
        name: str,
        started_at: datetime | None,
        ended_at: datetime,
    ) -> None:
        if started_at is None:
            return
        performance_diagnostics.record_component_duration(
            name,
            max(0.0, (ended_at - started_at).total_seconds() * 1000.0),
        )

    def population_metrics(
        self,
        *,
        active_symbols: tuple[str, ...] | None = None,
        streamed_symbols: tuple[str, ...] | None = None,
        now=None,
    ) -> dict[str, object]:
        metrics = getattr(self.adapter, "population_metrics", None)
        return (
            {}
            if not callable(metrics)
            else metrics(
                active_symbols=active_symbols,
                streamed_symbols=streamed_symbols,
                now=now,
            )
        )

    def pending_evaluation_symbols(self) -> tuple[str, ...]:
        mailbox = self._evaluation_mailbox
        if mailbox is None:
            return ()
        metrics = mailbox.metrics()
        return tuple(str(symbol) for symbol in metrics.get("pending_symbols", ()))

    def ranked(
        self,
        *,
        limit: int = 25,
    ) -> tuple[ScannerDecision, ...]:
        self._prune_stale(self._clock())
        return rank_candidates(
            self._latest.values(),
            limit=limit,
        )

    def all_latest(self) -> tuple[ScannerDecision, ...]:
        self._prune_stale(self._clock())
        return tuple(
            self._latest[symbol]
            for symbol in sorted(self._latest)
        )

    def _prune_stale(self, now: datetime) -> None:
        """Remove discovery snapshots that no longer have live evidence."""
        cutoff = now.timestamp() - self._candidate_retention_seconds
        stale = tuple(
            symbol for symbol, decision in self._latest.items()
            if decision.observed_at is None
            or decision.observed_at.timestamp() < cutoff
        )
        for symbol in stale:
            self._latest.pop(symbol, None)

    def close(self) -> None:
        close = getattr(self._decision_sink, "close", None)
        if callable(close):
            close()

    def diagnostic_results(self, *, limit: int = 3):
        return tuple(
            (result, self._latest.get(result.state.symbol))
            for result in self.adapter.diagnostic_results(limit=limit)
        )

    def qualification_diagnostics(
        self,
        *,
        example_limit: int = 3,
    ) -> QualificationDiagnostics:
        """Aggregate current per-symbol outcomes with bounded examples."""

        if example_limit < 0:
            raise ValueError("diagnostic example limit cannot be negative")
        decisions = self.all_latest()
        rejection_counts = tuple(
            (
                rule,
                sum(rule in decision.failed_rules for decision in decisions),
            )
            for rule in _QUALIFICATION_RULES
        )
        catalyst_counts = tuple(
            (
                status,
                sum(
                    decision.catalyst_status.value == status
                    for decision in decisions
                ),
            )
            for status in ("TRUE", "FALSE", "UNKNOWN", "UNAVAILABLE")
        )
        near = tuple(
            sorted(
                (
                    decision
                    for decision in decisions
                    if decision.failed_rules == ("news_catalyst",)
                ),
                key=lambda item: (-item.score, item.symbol),
            )
        )
        return QualificationDiagnostics(
            evaluated=self.adapter.state_count,
            complete=len(decisions),
            qualified=sum(decision.qualified for decision in decisions),
            rejection_counts=rejection_counts,
            catalyst_counts=catalyst_counts,
            otherwise_qualified_with_catalyst=len(near),
            near_qualified_symbols=tuple(
                decision.symbol for decision in near[:example_limit]
            ),
        )
