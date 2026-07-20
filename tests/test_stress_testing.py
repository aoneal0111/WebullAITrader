from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.analytics.distribution import analyze_distribution
from app.analytics.equity import analyze_equity
from app.analytics.models import (
    BacktestAnalyticsResult, ExperimentAnalyticsResult, ExperimentSuiteAnalyticsResult,
    ExposureAnalytics, RiskAnalytics, TradeOutcome, WalkForwardAnalyticsResult,
    WalkForwardWindowExperimentAnalytics,
)
from app.analytics.performance import analyze_trades
from app.backtesting.results import checkpoint_from_json
from app.market_history import MarketObservation
from app.paper_trading.models import EquityPoint
from app.stress_testing import (
    ExperimentSuiteStressTestResult, ScenarioFilter, ScenarioKind, StressTestConfig,
    WalkForwardStressTestResult, run_stress_test, stress_test_to_json, stress_test_to_text,
)

D = Decimal
T0 = datetime(2026, 1, 5, 14, tzinfo=UTC)


def analytics_source(*, observations: bool = True, trades: tuple[str, ...] = ("10", "-5", "2")):
    curve = tuple(EquityPoint(T0 + timedelta(hours=index), value)
                  for index, value in enumerate(map(D, ("100", "110", "105", "115"))))
    equity = analyze_equity(curve)
    outcomes = tuple(TradeOutcome(curve[index + 1].timestamp, D(value), D(value) > 0, D(value) < 0,
                                  D(value) == 0, None, None, "XYZ", str(index), index)
                     for index, value in enumerate(trades))
    history = () if not observations else tuple(
        MarketObservation(
            curve[index].timestamp, "XYZ", D(open_), D(high), D(low), D(close), D(volume),
            D(bid), D(ask), "REGULAR", status, D(slip), volatility, trend,
        )
        for index, (open_, high, low, close, volume, bid, ask, status, slip, volatility, trend) in enumerate((
            ("100", "111", "99", "110", "900", "109", "110", "OPEN", "0.1", "HIGH", "BEAR"),
            ("120", "121", "104", "105", "500", "104", "106", "HALTED", "0.8", "LOW", "TRENDING"),
            ("105", "116", "104", "115", "100", "114", "115", "OPEN", "0.2", "HIGH", "SIDEWAYS"),
        ), start=1)
    )
    empty = analyze_distribution(())
    exposure = ExposureAnalytics(False, None, None, None, None, None, None, None, None, None,
                                 None, None, None, None, ("missing",))
    risk = RiskAnalytics(None, None, None, None, None, None, None, None, None, None,
                         "equity_observation", 3)
    return BacktestAnalyticsResult(
        "source", "dataset", "config", "responses", "intents", equity, risk, exposure,
        analyze_trades(outcomes), analyze_distribution(tuple(item.return_value for item in equity.return_observations)),
        empty, empty, empty, (), (), (), (), (), (), (), (), completed_trade_outcomes=outcomes,
        market_observations=history,
    )


def configuration(kind: ScenarioKind, filter_: ScenarioFilter | None = None) -> StressTestConfig:
    return StressTestConfig((kind,), () if filter_ is None else (filter_,), D("0.000001"), 252, False)


@pytest.mark.parametrize(("kind", "filter_"), (
    (ScenarioKind.MARKET_CRASH, ScenarioFilter(ScenarioKind.MARKET_CRASH, minimum_drawdown=D("0.01"))),
    (ScenarioKind.BEAR_MARKET, None),
    (ScenarioKind.HIGH_VOLATILITY, None),
    (ScenarioKind.LOW_VOLATILITY, None),
    (ScenarioKind.TRENDING_MARKET, None),
    (ScenarioKind.SIDEWAYS_MARKET, None),
    (ScenarioKind.HIGH_SLIPPAGE, ScenarioFilter(ScenarioKind.HIGH_SLIPPAGE, minimum_absolute_slippage=D("0.5"))),
    (ScenarioKind.TRADING_HALTS, None),
    (ScenarioKind.CUSTOM, ScenarioFilter(ScenarioKind.CUSTOM, symbol="XYZ", weekdays=(1,))),
))
def test_every_builtin_scenario(kind, filter_) -> None:
    result = run_stress_test(analytics_source(), configuration(kind, filter_))
    scenario = result.scenarios[0]
    assert scenario.scenario is kind
    assert scenario.available
    assert scenario.metrics is not None


@pytest.mark.parametrize("kind", (ScenarioKind.GAP_HEAVY, ScenarioKind.LOW_LIQUIDITY, ScenarioKind.HIGH_SPREAD))
def test_unrecorded_authoritative_scenarios_fail_closed(kind) -> None:
    scenario = run_stress_test(analytics_source(), configuration(kind)).scenarios[0]
    assert not scenario.available
    assert "authoritative" in scenario.warnings[0]


def test_missing_authoritative_history_and_required_threshold_fail_closed() -> None:
    unavailable = run_stress_test(analytics_source(observations=False), configuration(ScenarioKind.HIGH_VOLATILITY))
    assert not unavailable.scenarios[0].available
    assert "required" in unavailable.scenarios[0].warnings[0]
    threshold = run_stress_test(analytics_source(), configuration(ScenarioKind.HIGH_SLIPPAGE))
    assert not threshold.scenarios[0].available
    assert "minimum_absolute_slippage" in threshold.scenarios[0].warnings[0]


def test_legacy_date_and_trade_outcome_filters_remain_supported() -> None:
    filter_ = ScenarioFilter(ScenarioKind.CUSTOM, start_timestamp=T0, end_timestamp=T0 + timedelta(hours=3),
                             outcome="WIN", symbol="XYZ")
    result = run_stress_test(analytics_source(observations=False), configuration(ScenarioKind.CUSTOM, filter_))
    assert result.scenarios[0].available
    assert result.scenarios[0].metrics.number_of_trades == 2


def test_single_trade_and_empty_dataset() -> None:
    result = run_stress_test(analytics_source(trades=("5",)), configuration(
        ScenarioKind.CUSTOM, ScenarioFilter(ScenarioKind.CUSTOM, outcome="WIN")))
    assert result.scenarios[0].metrics.number_of_trades == 1
    empty = replace(analytics_source(observations=False, trades=()),
                    equity=replace(analytics_source().equity, return_observations=()))
    failed = run_stress_test(empty, configuration(ScenarioKind.CUSTOM, ScenarioFilter(ScenarioKind.CUSTOM)))
    assert not failed.scenarios[0].available


def test_comparisons_reports_and_decimal_output_are_deterministic() -> None:
    config = configuration(ScenarioKind.HIGH_VOLATILITY)
    first = run_stress_test(analytics_source(), config)
    second = run_stress_test(analytics_source(), config)
    assert first == second
    assert stress_test_to_json(first) == stress_test_to_json(second)
    assert stress_test_to_text(first) == stress_test_to_text(second)
    assert '"absolute_difference":"' in stress_test_to_json(first)
    assert all(item.label in ("BETTER", "WORSE", "EQUAL", "UNAVAILABLE") for item in first.scenarios[0].comparisons)


def test_deterministic_scenario_and_adapter_ordering() -> None:
    source = analytics_source()
    config = StressTestConfig((ScenarioKind.LOW_VOLATILITY, ScenarioKind.BEAR_MARKET), (), D("0"), None, False)
    atomic = run_stress_test(source, config)
    assert tuple(item.scenario.value for item in atomic.scenarios) == tuple(sorted(item.scenario.value for item in atomic.scenarios))
    suite = ExperimentSuiteAnalyticsResult("dataset", (
        ExperimentAnalyticsResult("z", "z", source), ExperimentAnalyticsResult("a", "a", source)))
    suite_result = run_stress_test(suite, configuration(ScenarioKind.HIGH_VOLATILITY))
    assert isinstance(suite_result, ExperimentSuiteStressTestResult)
    assert tuple(item.experiment_id for item in suite_result.experiment_results) == ("a", "z")
    walk = WalkForwardAnalyticsResult("dataset", (
        WalkForwardWindowExperimentAnalytics(2, "z", source), WalkForwardWindowExperimentAnalytics(1, "a", source)), (), ())
    walk_result = run_stress_test(walk, configuration(ScenarioKind.HIGH_VOLATILITY))
    assert isinstance(walk_result, WalkForwardStressTestResult)
    assert tuple((item.window_index, item.experiment_id) for item in walk_result.window_results) == ((1, "a"), (2, "z"))


def test_market_observation_validation_and_ordering() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketObservation(datetime(2026, 1, 1), "X", D("1"), D("1"), D("1"), D("1"), D("0"), None, None, None, None, None)
    with pytest.raises(ValueError, match="OHLC"):
        MarketObservation(T0, "X", D("2"), D("1"), D("1"), D("2"), D("0"), None, None, None, None, None)
    with pytest.raises(ValueError, match="bid"):
        MarketObservation(T0, "X", D("1"), D("1"), D("1"), D("1"), D("0"), D("2"), D("1"), None, None, None)


def test_legacy_checkpoint_without_observations_migrates() -> None:
    from test_analytics import backtest
    payload = json.loads(backtest().checkpoint.to_json())
    payload.pop("market_observations", None)
    migrated = checkpoint_from_json(json.dumps(payload))
    assert migrated.market_observations == ()


def test_no_forbidden_execution_layer_imports() -> None:
    package = Path(__file__).parents[1] / "app" / "stress_testing"
    content = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    forbidden = ("app.ai", "app.strategy", "app.risk", "app.compliance", "app.order_compliance",
                 "app.paper_trading", "webull", "mcp")
    assert not any(f"from {name}" in content or f"import {name}" in content for name in forbidden)
