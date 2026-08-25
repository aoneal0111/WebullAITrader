from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from app.market_data.models import MarketEvent
from app.momentum_scanner.models import ScannerDecision
from app.momentum_scanner.ranking import rank_candidates
from app.momentum_scanner.rules import (
    MomentumScannerConfig,
    evaluate_candidate,
)
from app.scanner_adapter.adapter import MarketEventScannerAdapter
from app.scanner_adapter.models import QualificationDiagnostics


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

    def consume(
        self,
        event: MarketEvent,
    ) -> ScannerDecision | None:
        result = self.adapter.consume(event)

        if result is None or result.observation is None:
            return None

        decision = evaluate_candidate(
            result.observation,
            self.config,
        )
        decision = replace(
            decision,
            observed_at=self._clock(),
        )
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
        if self._decision_sink is not None:
            self._decision_sink(decision)

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
