from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.analytics import AnalyticsDataset, AnalyticsEngine, HistoricalTrade
from app.analytics.statistics import performance_metrics, risk_metrics

NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def trades() -> tuple[HistoricalTrade, ...]:
    return (
        HistoricalTrade(
            "one", "AAPL", "v2", "BUY", "APPROVED",
            "POSITION_CLOSE", NOW, NOW + timedelta(minutes=30),
            Decimal("100"),
        ),
        HistoricalTrade(
            "one", "MSFT", "v1", "BUY", "UNKNOWN",
            "EXIT", NOW, NOW + timedelta(minutes=60),
            Decimal("-40"),
        ),
    )


def test_performance_profit_factor_expectancy_and_duration() -> None:
    metrics = performance_metrics(trades())
    assert metrics.total_trades == 2
    assert metrics.winning_trades == 1
    assert metrics.losing_trades == 1
    assert metrics.win_rate == Decimal("0.5")
    assert metrics.profit_factor == Decimal("2.5")
    assert metrics.expectancy == Decimal("30")
    assert metrics.average_holding_duration == timedelta(minutes=45)
    assert metrics.net_realized_pnl == Decimal("60")


def test_drawdown_recovery_ulcer_and_exposure() -> None:
    equity = tuple(
        (NOW + timedelta(minutes=index), value)
        for index, value in enumerate(
            map(Decimal, ("1000", "900", "1200", "1000"))
        )
    )
    metrics = risk_metrics(
        equity,
        (Decimal("1000"), Decimal("2000")),
        Decimal("1500"),
        Decimal("60"),
    )
    assert metrics.maximum_drawdown == Decimal("200")
    assert metrics.rolling_drawdown == tuple(
        map(Decimal, ("0", "100", "0", "200"))
    )
    assert metrics.peak_equity == Decimal("1200")
    assert metrics.recovery_factor == Decimal("0.3")
    assert metrics.average_exposure == Decimal("1500")
    assert metrics.largest_position == Decimal("1500")
    assert Decimal("111") < metrics.ulcer_index < Decimal("112")


def test_engine_groups_strategy_symbol_decision_lifecycle_committee_and_time() -> None:
    result = AnalyticsEngine().analyze(
        AnalyticsDataset(
            trades=trades(),
            lifecycle_counts=(("EXIT", 1), ("POSITION_CLOSE", 1)),
        )
    )
    assert result.strategy.by_strategy_version == (
        ("v1", 1, Decimal("-40")),
        ("v2", 1, Decimal("100")),
    )
    assert result.strategy.by_decision == (("BUY", 2, Decimal("60")),)
    assert result.strategy.by_committee_outcome == (
        ("APPROVED", 1, Decimal("100")),
        ("UNKNOWN", 1, Decimal("-40")),
    )
    assert tuple(item.symbol for item in result.symbols) == ("AAPL", "MSFT")
    assert {item.dimension for item in result.time_metrics} == {
        "HOUR", "DAY", "WEEK", "MONTH", "TRADING_SESSION",
    }
