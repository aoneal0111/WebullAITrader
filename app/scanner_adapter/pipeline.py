from __future__ import annotations

from collections.abc import Iterable

from app.market_data.models import MarketEvent
from app.momentum_scanner.models import ScannerDecision
from app.momentum_scanner.ranking import rank_candidates
from app.momentum_scanner.rules import (
    MomentumScannerConfig,
    evaluate_candidate,
)
from app.scanner_adapter.adapter import MarketEventScannerAdapter


class MomentumScannerPipeline:
    def __init__(
        self,
        adapter: MarketEventScannerAdapter,
        config: MomentumScannerConfig = MomentumScannerConfig(),
    ) -> None:
        self.adapter = adapter
        self.config = config
        self._latest: dict[str, ScannerDecision] = {}

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
        self._latest[decision.symbol] = decision

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
