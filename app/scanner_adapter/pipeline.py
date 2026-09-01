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
from app.scanner_adapter.models import QualificationDiagnostics
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


class MomentumScannerPipeline:
    def __init__(
        self,
        adapter: MarketEventScannerAdapter,
        config: MomentumScannerConfig = MomentumScannerConfig(),
        *,
        decision_sink: Callable[[ScannerDecision], object] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.adapter = adapter
        self.config = config
        self._latest: dict[str, ScannerDecision] = {}
        self._clock = clock or (lambda: datetime.now(UTC))
        if decision_sink is not None and not callable(decision_sink):
            raise TypeError("decision_sink must be callable or None")
        self._decision_sink = decision_sink
        self._processing_delay_count = 0
        self._processing_delay_active = False

    def consume(
        self,
        event: MarketEvent,
    ) -> ScannerDecision | None:
        scanner_started = perf_counter()
        result = self.adapter.consume(event)

        if result is None or result.observation is None:
            return None

        decision = evaluate_candidate(
            result.observation,
            self.config,
        )
        observed_at = self._clock()
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
        self._latest[decision.symbol] = decision
        performance_diagnostics.record_scanner_duration(
            (perf_counter() - scanner_started) * 1000.0
        )
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

        return decision

    def consume_many(
        self,
        events: Iterable[MarketEvent],
    ) -> tuple[ScannerDecision, ...]:
        decisions: list[ScannerDecision] = []

        for event in events:
            decision = self.consume(event)
            if decision is not None:
                decisions.append(decision)

        return tuple(decisions)

    def reset_symbol(self, symbol: str) -> None:
        """Discard stream-derived state for one symbol."""
        normalized = symbol.strip().upper()
        self.adapter.reset_symbol(normalized)
        self._latest.pop(normalized, None)
        reset = getattr(self._decision_sink, "reset_symbol", None)
        if callable(reset):
            reset(normalized)

    def latest_decision(
        self,
        symbol: str,
    ) -> ScannerDecision | None:
        return self._latest.get(symbol.strip().upper())

    def ranked(
        self,
        *,
        limit: int = 25,
    ) -> tuple[ScannerDecision, ...]:
        return rank_candidates(
            self._latest.values(),
            limit=limit,
        )

    def all_latest(self) -> tuple[ScannerDecision, ...]:
        return tuple(
            self._latest[symbol]
            for symbol in sorted(self._latest)
        )

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
