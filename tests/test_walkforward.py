from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtesting.models import HistoricalCandle, HistoricalFrame, SuppliedAIResponse
from app.compliance.models import AccountType
from app.experiments.models import ExperimentDefinition
from app.order_compliance.kill_switch import KillSwitchState
from app.order_compliance.limits import DEFAULT_LIMITS
from app.order_compliance.models import MarketComplianceState, MarketStatus, SymbolStatus
from app.paper_trading.models import PaperExecutionConfig
from app.risk.limits import DEFAULT_RISK_LIMITS
from app.walkforward import (
    WalkForwardConfig, WalkForwardMode, run_walk_forward, split_walk_forward,
    walk_forward_to_json, walk_forward_to_text,
)

START = datetime(2026, 7, 20, 14, 30, tzinfo=UTC)


def _frames(count: int = 45) -> tuple[HistoricalFrame, ...]:
    result = []
    for index in range(count):
        opened = START + timedelta(minutes=index * 2)
        closed = opened + timedelta(minutes=1)
        price = Decimal(100 + index)
        candle = HistoricalCandle("TEST", opened, closed, price, price + 1, price - 1, price, Decimal(1000))
        market = MarketComplianceState(
            "TEST", MarketStatus.OPEN, SymbolStatus.TRADABLE,
            opened - timedelta(hours=1), closed + timedelta(hours=5),
            opened - timedelta(hours=2), closed + timedelta(hours=8),
            Decimal("0.01"), closed, price,
        )
        result.append(HistoricalFrame(candle, market, price, price, price))
    return tuple(result)


def _definition(identifier: str = "baseline") -> ExperimentDefinition:
    responses = tuple(
        SuppliedAIResponse(
            frame.candle.close_timestamp, "TEST",
            '{"action":"HOLD","confidence":80,"reason":"wait","stop_loss":null,"take_profit":null}',
        )
        for frame in _frames()
    )
    return ExperimentDefinition(
        identifier, "1.0", "1.0", responses, (), DEFAULT_RISK_LIMITS, DEFAULT_LIMITS,
        PaperExecutionConfig(120), AccountType.CASH, Decimal("10000"),
        KillSwitchState(False, "", None, ""),
    )


def test_rolling_windows_have_fixed_training_and_custom_step() -> None:
    windows = split_walk_forward(_frames(), WalkForwardConfig(WalkForwardMode.ROLLING, 20, 5, 5))
    assert [(item.training_range.start_index, item.training_range.end_index,
             item.evaluation_range.start_index, item.evaluation_range.end_index) for item in windows[:2]] == [
        (0, 20, 20, 25), (5, 25, 25, 30)
    ]


def test_expanding_windows_keep_training_start_at_zero() -> None:
    windows = split_walk_forward(_frames(), WalkForwardConfig(WalkForwardMode.EXPANDING, 20, 5, 5))
    assert windows[0].training_range.start_index == windows[1].training_range.start_index == 0
    assert windows[1].training_range.end_index == 25


def test_fixed_size_windows_are_non_overlapping() -> None:
    windows = split_walk_forward(_frames(), WalkForwardConfig(WalkForwardMode.FIXED_SIZE, 10, 5))
    assert [(item.training_range.start_index, item.evaluation_range.end_index) for item in windows] == [
        (0, 15), (15, 30), (30, 45)
    ]


def test_incomplete_trailing_window_is_omitted() -> None:
    windows = split_walk_forward(_frames(33), WalkForwardConfig(WalkForwardMode.ROLLING, 20, 5, 5))
    assert len(windows) == 2


@pytest.mark.parametrize(
    "config",
    [
        WalkForwardConfig(WalkForwardMode.ROLLING, 0, 5, 5),
        WalkForwardConfig(WalkForwardMode.ROLLING, 20, -1, 5),
        WalkForwardConfig(WalkForwardMode.ROLLING, 20, 5, True),
        WalkForwardConfig(WalkForwardMode.FIXED_SIZE, 10, 5, 1),
    ],
)
def test_invalid_split_configuration_fails_closed(config: WalkForwardConfig) -> None:
    with pytest.raises(ValueError):
        split_walk_forward(_frames(), config)


def test_insufficient_data_fails_closed() -> None:
    with pytest.raises(ValueError, match="complete"):
        split_walk_forward(_frames(10), WalkForwardConfig(WalkForwardMode.ROLLING, 10, 5, 5))


def test_runner_executes_independent_evaluation_windows() -> None:
    result = run_walk_forward(
        _frames(), (_definition(),), WalkForwardConfig(WalkForwardMode.ROLLING, 26, 5, 5)
    )
    assert len(result.runs) == 3
    assert len(result.aggregates) == 1
    assert result.aggregates[0].number_of_windows == 3
    for run in result.runs:
        backtest = run.experiment_results.experiment_results[0].backtest_result
        assert backtest.ending_cash == Decimal("10000")
        assert backtest.number_of_proposals == 0


def test_multiple_experiments_are_ordered_deterministically() -> None:
    result = run_walk_forward(
        _frames(), (_definition("z"), _definition("a")),
        WalkForwardConfig(WalkForwardMode.ROLLING, 26, 5, 5),
    )
    assert [item.experiment_id for item in result.aggregates] == ["a", "z"]
    assert all([item.experiment_id for item in run.experiment_results.experiment_results] == ["a", "z"] for run in result.runs)


def test_repeated_runs_and_reports_are_identical() -> None:
    args = (_frames(), (_definition(),), WalkForwardConfig(WalkForwardMode.ROLLING, 26, 5, 5))
    first, second = run_walk_forward(*args), run_walk_forward(*args)
    assert first == second
    assert walk_forward_to_json(first) == walk_forward_to_json(second)
    assert walk_forward_to_text(first) == walk_forward_to_text(second)
    assert "NO OPTIMIZATION OR WINNER SELECTION" in walk_forward_to_text(first)


def test_training_responses_are_filtered_out() -> None:
    result = run_walk_forward(
        _frames(), (_definition(),), WalkForwardConfig(WalkForwardMode.ROLLING, 26, 5, 5)
    )
    for run in result.runs:
        response_events = [event for event in run.experiment_results.experiment_results[0].backtest_result.checkpoint.replay_journal.events
                           if event.event_type.value == "AI_RESPONSE"]
        assert len(response_events) == 5
