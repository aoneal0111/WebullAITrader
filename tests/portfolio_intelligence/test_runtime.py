from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.portfolio_intelligence.configuration import (
    PortfolioIntelligenceConfiguration,
    PortfolioRiskLimits,
)
from app.portfolio_intelligence.events import MeaningfulChangeDetector
from app.portfolio_intelligence.models import (
    EquityObservation,
    OrderSide,
    PortfolioAccount,
    PortfolioFill,
    PortfolioIntelligenceInput,
    PortfolioPosition,
    PriceObservation,
    RiskBudgetClassification,
    WorkingOrder,
)
from app.portfolio_intelligence.runtime import PortfolioIntelligenceService


D = Decimal
UTC = timezone.utc
NOW = datetime(2026, 8, 6, 15, tzinfo=UTC)


def _source(**changes):
    values = dict(
        account=PortfolioAccount("account", D("400"), D("100"), D("100")),
        positions=(
            PortfolioPosition("ABC", D("10"), D("8"), D("10"), "EQUITY", strategy_id="s1", decision_type="ENTER"),
            PortfolioPosition("XYZ", D("-5"), D("22"), D("20"), "EQUITY"),
        ),
        working_orders=(WorkingOrder("o1", "ABC", OrderSide.BUY, D("2"), D("15")),),
        generated_at=NOW,
    )
    values.update(changes)
    return PortfolioIntelligenceInput(**values)


def test_exposure_weights_orders_and_decimal_precision():
    snapshot = PortfolioIntelligenceService().build(_source())
    exposure = snapshot.exposure
    assert exposure.gross_exposure == D("200")
    assert exposure.net_exposure == D("0")
    assert exposure.long_exposure == D("100")
    assert exposure.short_exposure == D("100")
    assert exposure.cash_percentage == D("0.25")
    assert exposure.buying_power_utilization == D("200") / D("300")
    assert dict(exposure.position_weights) == {"ABC": D("0.25"), "XYZ": D("0.25")}
    assert exposure.largest_position_weight == D("0.25")
    assert exposure.top_five_concentration == D("0.50")
    assert exposure.pending_order_exposure == D("30")
    assert exposure.gross_exposure_after_orders == D("230")
    assert exposure.net_exposure_after_orders == D("30")


def test_missing_marks_are_unknown_not_zero_and_zero_equity_is_safe():
    source = _source(
        account=PortfolioAccount("account", D("0"), D("0"), D("0")),
        positions=(PortfolioPosition("ABC", D("10"), D("8"), None),),
        working_orders=(),
    )
    snapshot = PortfolioIntelligenceService().build(source)
    assert snapshot.exposure.gross_exposure is None
    assert snapshot.exposure.position_weights == (("ABC", None),)
    assert snapshot.exposure.cash_percentage is None
    assert snapshot.concentration.hhi is None


def test_concentration_hhi_imbalance_dimensions_and_unknown_sector():
    snapshot = PortfolioIntelligenceService().build(_source())
    summary = snapshot.concentration
    assert summary.largest_symbol_allocation == D("0.25")
    assert summary.top_three_allocation == D("0.50")
    assert summary.top_five_allocation == D("0.50")
    assert summary.hhi == D("0.50")
    assert summary.long_short_imbalance == D("0")
    assert summary.strategy_concentration == (("Unattributed", D("0.5")), ("s1", D("0.5")))
    assert summary.asset_class_concentration == (("EQUITY", D("1")),)
    assert summary.sector_concentration is None


def test_correlation_includes_sufficient_overlap_and_excludes_other_pairs():
    config = PortfolioIntelligenceConfiguration(correlation_lookback=5, minimum_correlation_observations=3)
    times = tuple(NOW + timedelta(days=index) for index in range(5))
    history = {
        "ABC": tuple(PriceObservation(time, price) for time, price in zip(times, map(D, ("100", "110", "99", "118.8", "106.92")))),
        "XYZ": tuple(PriceObservation(time, price) for time, price in zip(times, map(D, ("50", "55", "49.5", "59.4", "53.46")))),
        "LOW": tuple(PriceObservation(time, price) for time, price in zip(times[:2], map(D, ("10", "11")))),
    }
    positions = _source().positions + (PortfolioPosition("LOW", D("1"), D("10"), D("10")),)
    snapshot = PortfolioIntelligenceService(config).build(_source(positions=positions, price_history=history))
    assert snapshot.correlation.highest_absolute_pair.correlation == D("1")
    assert snapshot.correlation.highly_correlated_pairs[0].first_symbol == "ABC"
    assert snapshot.correlation.eligible_pairs == 1
    assert snapshot.correlation.excluded_pairs == 2


def _fills():
    return (
        PortfolioFill("a-open", "ABC", OrderSide.BUY, D("2"), D("10"), NOW - timedelta(hours=4), D("0"), strategy_id="alpha", decision_type="ENTER", session_id="regular"),
        PortfolioFill("a-close", "ABC", OrderSide.SELL, D("2"), D("12"), NOW - timedelta(hours=2), D("4"), strategy_id="alpha", decision_type="EXIT", session_id="regular"),
        PortfolioFill("b-open", "XYZ", OrderSide.BUY, D("1"), D("20"), NOW - timedelta(hours=3), D("0")),
        PortfolioFill("b-close", "XYZ", OrderSide.SELL, D("1"), D("17"), NOW - timedelta(hours=1), D("-3")),
    )


def test_performance_trade_grouping_attribution_drawdown_and_no_duplicates():
    equity = (
        EquityObservation(NOW - timedelta(hours=4), D("100")),
        EquityObservation(NOW - timedelta(hours=3), D("120")),
        EquityObservation(NOW - timedelta(hours=2), D("90")),
        EquityObservation(NOW, D("100")),
    )
    fills = _fills() + (_fills()[1],)
    snapshot = PortfolioIntelligenceService().build(_source(fills=fills, equity_history=equity))
    result = snapshot.performance
    assert result.trade_count == 2
    assert result.win_rate == D("0.5")
    assert result.loss_rate == D("0.5")
    assert result.gross_profit == D("4")
    assert result.gross_loss == D("-3")
    assert result.profit_factor == D("4") / D("3")
    assert result.average_win == D("4")
    assert result.average_loss == D("-3")
    assert result.expectancy == D("0.5")
    assert result.average_holding_period_seconds == D("7200")
    assert result.maximum_drawdown == D("0.25")
    assert result.current_drawdown == D("1") / D("6")
    assert result.return_on_equity == D("0")
    assert result.cumulative_realized_pnl == D("1")
    strategy = {entry.key: entry for entry in snapshot.attribution.by_strategy}
    assert strategy["alpha"].realized_pnl == D("4")
    assert strategy["Unattributed"].realized_pnl == D("-3")


def test_day_boundary_and_unknown_daily_unrealized():
    config = PortfolioIntelligenceConfiguration(
        correlation_lookback=20,
        minimum_correlation_observations=2,
        performance_reporting_timezone="America/Chicago",
        trading_day_boundary_hour=4,
    )
    before_boundary = datetime(2026, 8, 6, 8, 30, tzinfo=UTC)  # 03:30 local, prior trading day
    after_boundary = datetime(2026, 8, 6, 9, 30, tzinfo=UTC)
    fills = (
        PortfolioFill("old", "ABC", OrderSide.BUY, D("1"), D("1"), before_boundary, D("5")),
        PortfolioFill("new", "ABC", OrderSide.BUY, D("1"), D("1"), after_boundary, D("7")),
    )
    snapshot = PortfolioIntelligenceService(config).build(_source(fills=fills, generated_at=NOW))
    assert snapshot.performance.daily_realized_pnl == D("7")
    assert snapshot.performance.daily_unrealized_pnl is None
    assert snapshot.performance.daily_total_pnl is None


@pytest.mark.parametrize(
    ("current", "expected"),
    ((D("79"), RiskBudgetClassification.WITHIN_LIMITS), (D("80"), RiskBudgetClassification.APPROACHING_LIMIT), (D("100"), RiskBudgetClassification.AT_LIMIT), (D("101"), RiskBudgetClassification.EXCEEDED)),
)
def test_risk_budget_classifications(current, expected):
    limits = PortfolioRiskLimits(maximum_gross_exposure=D("100"))
    source = _source(positions=(PortfolioPosition("ABC", D("1"), D("1"), current),), working_orders=())
    metric = PortfolioIntelligenceService(limits=limits).build(source).risk_budget.metrics[0]
    assert metric.classification is expected


def test_missing_limits_are_unknown_and_unattributed_is_explicit():
    snapshot = PortfolioIntelligenceService().build(_source(fills=_fills()))
    assert snapshot.risk_budget.overall is RiskBudgetClassification.UNKNOWN
    assert any(entry.key == "Unattributed" for entry in snapshot.attribution.by_decision_type)


def test_meaningful_change_detector_suppresses_price_tick_without_transition():
    service = PortfolioIntelligenceService()
    first = service.build(_source())
    second = replace(first, generated_at=NOW + timedelta(seconds=1))
    assert MeaningfulChangeDetector().detect(first, second) == ()


def test_configuration_validation():
    with pytest.raises(ValueError):
        PortfolioIntelligenceConfiguration(minimum_correlation_observations=1)
    with pytest.raises(ValueError):
        PortfolioIntelligenceConfiguration(performance_reporting_timezone="not/a-zone")
