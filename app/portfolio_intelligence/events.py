"""Structured, transition-based portfolio observation events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from .models import PortfolioIntelligenceSnapshot


class PortfolioObservationType(StrEnum):
    EXPOSURE_THRESHOLD_CROSSED = "EXPOSURE_THRESHOLD_CROSSED"
    CONCENTRATION_CLASSIFICATION_CHANGED = "CONCENTRATION_CLASSIFICATION_CHANGED"
    NEW_MAXIMUM_DRAWDOWN = "NEW_MAXIMUM_DRAWDOWN"
    RISK_BUDGET_CLASSIFICATION_CHANGED = "RISK_BUDGET_CLASSIFICATION_CHANGED"
    HIGH_CORRELATION_PAIR_DETECTED = "HIGH_CORRELATION_PAIR_DETECTED"
    PERFORMANCE_SUMMARY_UPDATED = "PERFORMANCE_SUMMARY_UPDATED"


@dataclass(frozen=True, slots=True)
class PortfolioObservationEvent:
    event_type: PortfolioObservationType
    occurred_at: datetime
    message: str
    key: str


class MeaningfulChangeDetector:
    """Emit only threshold crossings, classifications, and completed-trade changes."""

    def __init__(
        self,
        *,
        exposure_step: Decimal = Decimal("0.05"),
        concentration_warning: Decimal = Decimal("0.50"),
        concentration_critical: Decimal = Decimal("0.75"),
    ) -> None:
        self.exposure_step = Decimal(exposure_step)
        self.concentration_warning = Decimal(concentration_warning)
        self.concentration_critical = Decimal(concentration_critical)
        if not self.exposure_step.is_finite() or self.exposure_step <= 0:
            raise ValueError("exposure_step must be positive")
        if not Decimal("0") < self.concentration_warning <= self.concentration_critical <= Decimal("1"):
            raise ValueError("concentration thresholds must be ordered in (0, 1]")

    def detect(
        self,
        previous: PortfolioIntelligenceSnapshot | None,
        current: PortfolioIntelligenceSnapshot,
    ) -> tuple[PortfolioObservationEvent, ...]:
        if previous is None:
            return ()
        events: list[PortfolioObservationEvent] = []
        old_gross, new_gross = previous.exposure.gross_exposure, current.exposure.gross_exposure
        equity = current.account.equity
        if old_gross is not None and new_gross is not None and equity not in (None, Decimal("0")):
            old_bucket = int((old_gross / equity) / self.exposure_step)
            new_bucket = int((new_gross / equity) / self.exposure_step)
            if old_bucket != new_bucket:
                events.append(self._event(current, PortfolioObservationType.EXPOSURE_THRESHOLD_CROSSED, "gross-exposure", "Gross exposure crossed a reporting threshold."))
        old_concentration = self._concentration_class(previous)
        new_concentration = self._concentration_class(current)
        if old_concentration != new_concentration:
            events.append(self._event(current, PortfolioObservationType.CONCENTRATION_CLASSIFICATION_CHANGED, "concentration", f"Concentration changed to {new_concentration}."))
        old_drawdown = previous.performance.maximum_drawdown
        new_drawdown = current.performance.maximum_drawdown
        if new_drawdown is not None and old_drawdown is not None and new_drawdown > old_drawdown:
            events.append(self._event(current, PortfolioObservationType.NEW_MAXIMUM_DRAWDOWN, "maximum-drawdown", "A new maximum portfolio drawdown was observed."))
        if previous.risk_budget.overall != current.risk_budget.overall:
            events.append(self._event(current, PortfolioObservationType.RISK_BUDGET_CLASSIFICATION_CHANGED, "risk-budget", f"Portfolio risk budget changed to {current.risk_budget.overall.value}."))
        old_pairs = {_pair_key(pair) for pair in previous.correlation.highly_correlated_pairs}
        for pair in current.correlation.highly_correlated_pairs:
            key = _pair_key(pair)
            if key not in old_pairs:
                events.append(self._event(current, PortfolioObservationType.HIGH_CORRELATION_PAIR_DETECTED, key, f"High correlation detected between {pair.first_symbol} and {pair.second_symbol}."))
        if (previous.performance.trade_count, previous.performance.cumulative_realized_pnl) != (current.performance.trade_count, current.performance.cumulative_realized_pnl):
            events.append(self._event(current, PortfolioObservationType.PERFORMANCE_SUMMARY_UPDATED, "performance", "Portfolio performance summary updated."))
        return tuple(events)

    @staticmethod
    def _event(snapshot, event_type, key, message):
        return PortfolioObservationEvent(event_type, snapshot.generated_at, message, key)

    def _concentration_class(self, snapshot: PortfolioIntelligenceSnapshot) -> str:
        value = snapshot.concentration.top_five_allocation
        if value is None:
            return "Unknown"
        if value >= self.concentration_critical:
            return "High"
        if value >= self.concentration_warning:
            return "Moderate"
        return "Low"


def _pair_key(pair) -> str:
    return ":".join(sorted((pair.first_symbol, pair.second_symbol)))


def portfolio_observation_event_id(observation: PortfolioObservationEvent) -> UUID:
    """Stable identity for live/replay publication of one observation."""
    return uuid5(
        NAMESPACE_URL,
        f"atlas:portfolio-observation:{observation.event_type.value}:{observation.key}:{observation.occurred_at.isoformat()}",
    )
