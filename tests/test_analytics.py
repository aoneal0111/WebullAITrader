from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.analytics import AnalyticsConfig, analyze_backtest, analytics_to_json, analytics_to_text
from app.analytics.distribution import (
    analyze_distribution, median, percentile, population_standard_deviation, population_variance,
)
from app.analytics.equity import (
    analyze_equity, calculate_daily_returns, calculate_monthly_returns, calculate_weekly_returns,
    identify_drawdown_episodes, validate_equity_curve,
)
from app.analytics.exposure import analyze_exposure
from app.analytics.models import ReturnObservation
from app.analytics.risk import analyze_risk
from app.backtesting.models import ReplayCheckpoint, ReplayJournal
from app.backtesting.results import BacktestResult, checkpoint_from_json
from app.paper_trading.models import (
    EquityPoint, JournalEvent, JournalEventType, PaperJournal, PaperPortfolio, PaperPosition,
)

D = Decimal
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def points(*values: str, spacing: timedelta = timedelta(days=1)) -> tuple[EquityPoint, ...]:
    return tuple(EquityPoint(T0 + index * spacing, D(value)) for index, value in enumerate(values))


def portfolio(timestamp: datetime, equity: str, *, invested: str = "0") -> PaperPortfolio:
    value = D(invested)
    positions = () if value == 0 else (PaperPosition("XYZ", D("1"), value, value, value, D("0")),)
    return PaperPortfolio(D("100"), D(equity) - value, positions, D("0"), D("0"), D(equity), timestamp)


def backtest(curve: tuple[EquityPoint, ...] | None = None) -> BacktestResult:
    curve = curve or points("100", "110", "105")
    history = tuple(portfolio(item.timestamp, str(item.equity), invested="50" if index else "0")
                    for index, item in enumerate(curve))
    events = (
        JournalEvent(1, JournalEventType.FILL, "a", curve[-1].timestamp, "fill",
                     (("side", "SELL"), ("symbol", "XYZ"), ("realized_pnl", "10"))),
        JournalEvent(2, JournalEventType.FILL, "b", curve[-1].timestamp, "fill",
                     (("side", "SELL"), ("symbol", "XYZ"), ("realized_pnl", "-5"))),
        JournalEvent(3, JournalEventType.FILL, "c", curve[-1].timestamp, "fill",
                     (("side", "SELL"), ("symbol", "XYZ"), ("realized_pnl", "0"))),
    )
    checkpoint = ReplayCheckpoint(2, "dataset", "responses", "intents", "config", 3, history[-1],
                                  PaperJournal(events), ReplayJournal(), curve, history, (), None, 3, 3, 0, 3)
    return BacktestResult(curve[0].timestamp, curve[-1].timestamp, 3, 3, 3, 0, 3,
                          history[-1].cash, curve[-1].equity, D("5"), D("0"),
                          curve[-1].equity / curve[0].equity - 1, D("0.05"), D("50"), D("2"),
                          D("1.666666666666666666666666667"), checkpoint)


def test_equity_validation_and_exact_returns() -> None:
    curve = points("100", "110", "99")
    result = analyze_equity(curve)
    assert result.return_observations[0].return_value == D("0.1")
    assert result.return_observations[1].return_value == D("-0.1")
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_equity_curve((curve[0], EquityPoint(curve[0].timestamp, D("101"))))
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_equity_curve((EquityPoint(datetime(2026, 1, 1), D("100")),))


def test_period_grouping_uses_utc_period_ends_without_synthetic_periods() -> None:
    curve = (
        EquityPoint(datetime(2026, 1, 1, 1, tzinfo=UTC), D("100")),
        EquityPoint(datetime(2026, 1, 1, 22, tzinfo=UTC), D("105")),
        EquityPoint(datetime(2026, 1, 8, 1, tzinfo=UTC), D("110")),
        EquityPoint(datetime(2026, 2, 2, 1, tzinfo=UTC), D("121")),
    )
    assert tuple(item.return_value for item in calculate_daily_returns(curve)) == (D("110") / D("105") - 1, D("0.1"))
    assert len(calculate_weekly_returns(curve)) == 2
    assert len(calculate_monthly_returns(curve)) == 1


def test_drawdown_episode_semantics_and_durations() -> None:
    curve = points("100", "80", "90", "100", spacing=timedelta(seconds=1))
    episode = identify_drawdown_episodes(curve)[0]
    assert (episode.peak_timestamp, episode.trough_timestamp, episode.recovery_timestamp) == (curve[0].timestamp, curve[1].timestamp, curve[3].timestamp)
    assert episode.drawdown == D("0.2")
    assert episode.decline_duration_microseconds == 1_000_000
    assert episode.recovery_duration_microseconds == 2_000_000
    assert episode.total_underwater_duration_microseconds == 3_000_000
    analysis = analyze_equity(curve)
    assert analysis.maximum_drawdown == analysis.average_drawdown == D("0.2")
    assert analysis.current_drawdown == 0


def test_unrecovered_and_multiple_drawdowns() -> None:
    curve = points("100", "90", "100", "110", "88")
    result = analyze_equity(curve)
    assert result.recovered_episode_count == 1
    assert result.unrecovered_episode_count == 1
    assert result.current_drawdown == D("0.2")
    assert result.maximum_drawdown == D("0.2")


def test_risk_population_formulas_and_annualization() -> None:
    curve = points("100", "110", "99")
    equity = analyze_equity(curve)
    risk = analyze_risk(equity.return_observations, equity, curve[0].timestamp, curve[-1].timestamp,
                        AnalyticsConfig(annualization_periods=4))
    assert risk.arithmetic_mean_return == 0
    assert risk.return_standard_deviation == D("0.1")
    assert risk.downside_deviation == D("0.070710678118654752440084436210484903928483593768847")
    assert risk.annualized_volatility == D("0.2")
    assert risk.annualized_sharpe_ratio == 0
    flat = (ReturnObservation(T0, D("0.01")), ReturnObservation(T0 + timedelta(days=1), D("0.01")))
    assert analyze_risk(flat, equity, curve[0].timestamp, curve[-1].timestamp, AnalyticsConfig()).period_sharpe_ratio is None


def test_distribution_decimal_linear_interpolation_and_population_statistics() -> None:
    values = (D("1"), D("2"), D("3"), D("4"))
    assert median(values) == D("2.5")
    assert percentile(values, D("0.25")) == D("1.75")
    assert population_variance(values) == D("1.25")
    assert population_standard_deviation(values) == D("1.1180339887498948482045868343656381177203091798058")
    distribution = analyze_distribution(values)
    assert distribution.percentile_01 == D("1.03")
    assert distribution.skewness == 0
    assert analyze_distribution(()).mean is None
    assert analyze_distribution((D("1"),)).excess_kurtosis is None


def test_interval_weighted_exposure_and_missing_prerequisite() -> None:
    curve = points("100", "100", "100", spacing=timedelta(seconds=1))
    history = (portfolio(curve[0].timestamp, "100"), portfolio(curve[1].timestamp, "100", invested="100"),
               portfolio(curve[2].timestamp, "100", invested="100"))
    result = analyze_exposure(history, curve)
    assert result.time_in_market_percent == D("50")
    assert result.average_gross_exposure_percent == D("50")
    assert result.maximum_gross_exposure_percent == D("100")
    assert result.average_holding_duration_microseconds is None
    assert not analyze_exposure(None, curve).available
    with pytest.raises(ValueError, match="aligned"):
        analyze_exposure(tuple(reversed(history)), curve)


def test_trade_metrics_use_realized_sell_fills_and_deterministic_ties() -> None:
    result = analyze_backtest(backtest())
    trades = result.trades
    assert (trades.winning_outcomes, trades.losing_outcomes, trades.breakeven_outcomes) == (1, 1, 1)
    assert trades.win_rate == trades.loss_rate == D("50")
    assert trades.gross_profit == D("10") and trades.gross_loss == D("5")
    assert trades.profit_factor == D("2")
    assert trades.expectancy == D("5") / D("3")
    assert trades.payoff_ratio == D("2")
    assert trades.maximum_consecutive_wins == trades.maximum_consecutive_losses == 1


def test_analysis_and_reports_are_repeatable_and_exact() -> None:
    source = backtest()
    first = analyze_backtest(source, AnalyticsConfig(rolling_window=2))
    second = analyze_backtest(source, AnalyticsConfig(rolling_window=2))
    assert first == second
    assert analytics_to_json(first) == analytics_to_json(second)
    payload = json.loads(analytics_to_json(first))
    assert payload["analytics"]["equity"]["starting_equity"] == "100"
    text = analytics_to_text(first)
    assert "N/A" in text
    assert "HISTORICAL PAPER-SIMULATION ANALYTICS ONLY" in text


def test_checkpoint_round_trip_preserves_portfolio_history() -> None:
    checkpoint = backtest().checkpoint
    assert checkpoint_from_json(checkpoint.to_json()) == checkpoint


def test_invalid_config_and_no_float_outputs() -> None:
    with pytest.raises(ValueError):
        analyze_backtest(backtest(), AnalyticsConfig(annualization_periods=0))
    result = analyze_backtest(backtest())

    def visit(value: object) -> None:
        assert not isinstance(value, float)
        if hasattr(value, "__dataclass_fields__"):
            for name in value.__dataclass_fields__:
                visit(getattr(value, name))
        elif isinstance(value, (tuple, list)):
            for item in value:
                visit(item)

    visit(result)
