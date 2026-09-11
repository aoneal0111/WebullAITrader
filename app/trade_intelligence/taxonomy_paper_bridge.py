"""PAPER-only bridge from research opportunities to Warrior's existing seam."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app.opportunity_discovery import (
    CompletedBar, DiscoveryContext, FeatureCapabilities,
    MultiStrategyDiscoveryEngine, MultiStrategyExecutionAdapter,
)
from app.performance_diagnostics import performance_diagnostics


class TaxonomyPaperExecutionBridge:
    """Adapt bounded research output without owning authorization or orders."""

    def __init__(self, adapter: MultiStrategyExecutionAdapter | None = None) -> None:
        self.adapter = adapter or MultiStrategyExecutionAdapter()
        self.discovery = MultiStrategyDiscoveryEngine()

    def evaluate(self, value, legacy_candidate, legacy_signal, stale, runtime, stale_after):
        if stale:
            return None, None
        try:
            context = _discovery_context(value)
            batch = self.discovery.observe(context)
            for opportunity in batch.opportunities:
                result = self.adapter.adapt(
                    opportunity,
                    strategy_scores={
                        item.strategy_id: legacy_candidate.score.total
                        for item in opportunity.memberships
                    },
                    observed_at=(
                        value.evaluation_timestamp or value.observation.timestamp
                    ),
                    freshness_max_age=timedelta(seconds=float(stale_after)),
                    freshness_authority=value.quote_provenance,
                    spread_percent=legacy_candidate.spread_percent,
                    dollar_volume=legacy_candidate.dollar_volume,
                )
                if result.candidate is None:
                    continue
                try:
                    setup = result.candidate.as_warrior_setup()
                except (TypeError, ValueError):
                    _record_failure(legacy_candidate.symbol, "MISSING_WARRIOR_PROJECTION")
                    continue
                assessed, signal = runtime.assess_entry(
                    replace(legacy_candidate, setup=setup)
                )
                if signal is None:
                    continue
                performance_diagnostics.record_strategy_selection(
                    symbol=legacy_candidate.symbol,
                    strategies_evaluated=tuple(
                        item.strategy_id for item in opportunity.memberships
                    ),
                    strategy_memberships=result.candidate.strategy_memberships,
                    selected_execution_strategy=result.candidate.selected_execution_strategy,
                    opportunity_anchor=result.candidate.opportunity_anchor,
                    execution_identity=result.candidate.execution_identity,
                    composition_outcome=(
                        "SAME_OPPORTUNITY_DEDUPED"
                        if legacy_signal is not None else "TAXONOMY_SELECTED"
                    ),
                    arbitration_winner=(
                        "LEGACY_WARRIOR"
                        if legacy_signal is not None
                        else result.candidate.selected_execution_strategy
                    ),
                    suppressed_candidate=(
                        None if legacy_signal is None else legacy_signal.setup_type.value
                    ),
                )
                if legacy_signal is not None:
                    return None, None
                return assessed, signal
        except Exception:
            _record_failure(legacy_candidate.symbol, "ADAPTER_EXCEPTION_FAIL_CLOSED")
        return None, None


def _discovery_context(value):
    observation = value.observation
    bars = tuple(
        CompletedBar(
            bar.symbol, bar.timestamp, bar.open, bar.high, bar.low,
            bar.close, bar.volume, value.session,
        )
        for bar in value.bars
    )
    regular_bars = tuple(bar for bar in bars if bar.session.upper() == "REGULAR")
    previous_close = observation.previous_close
    return DiscoveryContext(
        symbol=observation.symbol,
        session_date=observation.timestamp.date(),
        session=value.session,
        decision_cutoff=observation.timestamp,
        completed_bars=bars,
        capabilities=FeatureCapabilities(
            completed_bars=bool(bars), impulse_history=bool(bars),
            pullback_history=bool(bars), session_hod=bool(bars),
            opening_range=len(regular_bars) >= 5,
            premarket_history=False,
            prior_close=previous_close is not None,
        ),
        prior_close=previous_close,
        percentage_change=(
            None if previous_close <= Decimal("0") else
            observation.price / previous_close * Decimal("100") - Decimal("100")
        ),
        relative_volume=(
            None if observation.average_30_day_volume <= Decimal("0") else
            observation.current_volume / observation.average_30_day_volume
        ),
        dollar_volume=observation.price * observation.current_volume,
        spread_percent=(
            None if observation.bid is None or observation.ask is None or observation.price <= Decimal("0") else
            (observation.ask - observation.bid) / observation.price * Decimal("100")
        ),
        float_shares=observation.float_shares,
    )


def _record_failure(symbol: str, reason: str) -> None:
    performance_diagnostics.record_strategy_selection(
        symbol=symbol,
        composition_outcome="NO_EXECUTION_VALID_TAXONOMY_CANDIDATE",
        adapter_rejection_reason=reason,
    )


__all__ = ["TaxonomyPaperExecutionBridge"]
