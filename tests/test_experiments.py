from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtesting.models import BacktestOrderIntent, HistoricalCandle, HistoricalFrame, SuppliedAIResponse
from app.compliance.models import AccountType
from app.experiments import comparison_to_json, comparison_to_text, run_experiment, run_experiments
from app.experiments.models import ExperimentDefinition
from app.order_compliance.kill_switch import KillSwitchState
from app.order_compliance.limits import DEFAULT_LIMITS
from app.order_compliance.models import (
    MarketComplianceState, MarketStatus, OrderSide, OrderType, SymbolStatus, TradingSession,
)
from app.paper_trading.models import PaperExecutionConfig
from app.risk.limits import DEFAULT_RISK_LIMITS, RiskLimits

START = datetime(2026, 7, 20, 14, 30, tzinfo=UTC)


def _frames() -> tuple[HistoricalFrame, ...]:
    result = []
    for index in range(28):
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
        result.append(HistoricalFrame(candle, market, price - Decimal("0.01"), price + Decimal("0.01"), price))
    return tuple(result)


def _definition(identifier: str, *, action: str = "HOLD", notes: str = "") -> ExperimentDefinition:
    frames = _frames()
    timestamp = frames[25].candle.close_timestamp
    price = frames[25].candle.close
    if action == "HOLD":
        raw = '{"action":"HOLD","confidence":80,"reason":"wait","stop_loss":null,"take_profit":null}'
        intents = ()
    elif action == "BUY":
        raw = f'{{"action":"BUY","confidence":80,"reason":"buy","stop_loss":{price-2},"take_profit":{price+4}}}'
        intents = (BacktestOrderIntent(timestamp, f"{identifier}-1", "TEST", OrderSide.BUY, Decimal(1), OrderType.MARKET, None, None, TradingSession.REGULAR),)
    else:
        raw = f'{{"action":"SELL","confidence":80,"reason":"sell","stop_loss":{price+2},"take_profit":{price-4}}}'
        intents = (BacktestOrderIntent(timestamp, f"{identifier}-1", "TEST", OrderSide.SELL, Decimal(1), OrderType.MARKET, None, None, TradingSession.REGULAR),)
    return ExperimentDefinition(
        identifier, "1.0", "1.0", (SuppliedAIResponse(timestamp, "TEST", raw),), intents,
        DEFAULT_RISK_LIMITS, DEFAULT_LIMITS, PaperExecutionConfig(120), AccountType.CASH,
        Decimal("10000"), KillSwitchState(False, "", None, ""), notes,
    )


def test_runs_one_experiment_through_backtesting() -> None:
    result = run_experiment(_frames(), _definition("buy", action="BUY"))
    assert result.backtest_result.number_filled == 1
    assert result.runtime.candles_processed == len(_frames())
    assert result.runtime.historical_microseconds > 0


def test_multiple_experiments_share_dataset_and_sort_by_id() -> None:
    suite = run_experiments(_frames(), (_definition("z"), _definition("a", action="BUY")))
    assert [item.experiment_id for item in suite.experiment_results] == ["a", "z"]
    assert all(item.dataset_fingerprint == suite.dataset_fingerprint for item in suite.experiment_results)


def test_configuration_changes_fingerprint_but_notes_do_not() -> None:
    frames = _frames()
    base = run_experiment(frames, _definition("same", notes="first"))
    renamed_notes = run_experiment(frames, _definition("same", notes="second"))
    changed_risk = replace(_definition("same"), risk_configuration=RiskLimits(75, Decimal(2), Decimal(5), Decimal("2.5")))
    assert base.configuration_fingerprint == renamed_notes.configuration_fingerprint
    assert base.configuration_fingerprint != run_experiment(frames, changed_risk).configuration_fingerprint


def test_gfv_and_compliance_rejections_are_counted_from_journal() -> None:
    suite = run_experiments(_frames(), (_definition("sell", action="SELL"),))
    row = suite.comparison_rows[0]
    assert row.number_of_gfv_rejections == 1
    assert row.number_of_compliance_rejections == 1
    assert row.number_of_rejected_proposals == 1


def test_reports_are_canonical_and_do_not_select_winner() -> None:
    suite = run_experiments(_frames(), (_definition("b"), _definition("a", action="BUY")))
    assert comparison_to_json(suite) == comparison_to_json(suite)
    assert comparison_to_text(suite) == comparison_to_text(suite)
    assert "NO WINNER IS SELECTED" in comparison_to_text(suite)


def test_repeated_suites_are_identical() -> None:
    definitions = (_definition("a", action="BUY"), _definition("b"))
    assert run_experiments(_frames(), definitions) == run_experiments(_frames(), definitions)


def test_empty_and_duplicate_experiment_sets_are_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        run_experiments(_frames(), ())
    with pytest.raises(ValueError, match="unique"):
        run_experiments(_frames(), (_definition("same"), _definition("same")))


def test_malformed_definition_fails_closed() -> None:
    with pytest.raises(ValueError, match="initial cash"):
        run_experiment(_frames(), replace(_definition("bad"), initial_cash=Decimal("NaN")))
