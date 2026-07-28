from __future__ import annotations

from dataclasses import dataclass

from .models import (
    PerformanceMetrics,
    RiskMetrics,
    StrategyMetrics,
    SymbolMetrics,
    TimeMetrics,
)
from .repository import AnalyticsDataset
from .statistics import (
    grouped_performance,
    performance_metrics,
    risk_metrics,
    time_metrics,
)


@dataclass(frozen=True, slots=True)
class AnalyticsResultSet:
    performance: PerformanceMetrics
    risk: RiskMetrics
    strategy: StrategyMetrics
    symbols: tuple[SymbolMetrics, ...]
    time_metrics: tuple[TimeMetrics, ...]


class AnalyticsEngine:
    def analyze(self, dataset: AnalyticsDataset) -> AnalyticsResultSet:
        if not isinstance(dataset, AnalyticsDataset):
            raise TypeError("dataset must be AnalyticsDataset")
        performance = performance_metrics(dataset.trades)
        return AnalyticsResultSet(
            performance=performance,
            risk=risk_metrics(
                dataset.equity,
                dataset.exposures,
                dataset.largest_position,
                performance.net_realized_pnl,
            ),
            strategy=StrategyMetrics(
                by_strategy_version=grouped_performance(
                    dataset.trades,
                    lambda trade: trade.strategy_version,
                ),
                by_decision=grouped_performance(
                    dataset.trades,
                    lambda trade: trade.decision,
                ),
                by_lifecycle_phase=dataset.lifecycle_counts,
                by_committee_outcome=grouped_performance(
                    dataset.trades,
                    lambda trade: trade.committee_outcome,
                ),
            ),
            symbols=tuple(
                SymbolMetrics(
                    symbol,
                    performance_metrics(
                        tuple(
                            trade
                            for trade in dataset.trades
                            if trade.symbol == symbol
                        )
                    ),
                )
                for symbol in sorted(
                    {trade.symbol for trade in dataset.trades}
                )
            ),
            time_metrics=time_metrics(dataset.trades),
        )
