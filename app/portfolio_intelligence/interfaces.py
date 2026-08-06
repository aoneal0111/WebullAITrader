from __future__ import annotations

from typing import Mapping, Protocol

from .models import CorrelationSummary, PortfolioPosition, PriceObservation


class CorrelationAnalyzer(Protocol):
    def analyze(self, positions: tuple[PortfolioPosition, ...], history: Mapping[str, tuple[PriceObservation, ...]]) -> CorrelationSummary: ...
