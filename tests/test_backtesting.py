from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtesting import checkpoint_from_json, resume_backtest, run_backtest, run_until
from app.backtesting.models import (
    BacktestConfig, BacktestOrderIntent, HistoricalCandle, HistoricalFrame, SuppliedAIResponse,
)
from app.compliance.models import AccountType
from app.order_compliance.kill_switch import KillSwitchState
from app.order_compliance.limits import DEFAULT_LIMITS
from app.order_compliance.models import (
    MarketComplianceState, MarketStatus, OrderSide, OrderType, SymbolStatus, TradingSession,
)
from app.paper_trading.models import PaperExecutionConfig

START = datetime(2026, 7, 20, 14, 30, tzinfo=UTC)


def _frames(count: int = 30) -> tuple[HistoricalFrame, ...]:
    frames = []
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
        frames.append(HistoricalFrame(candle, market, price - Decimal("0.01"), price + Decimal("0.01"), price))
    return tuple(frames)


def _config() -> BacktestConfig:
    return BacktestConfig(
        AccountType.CASH, Decimal("10000"), DEFAULT_LIMITS, PaperExecutionConfig(120),
        KillSwitchState(False, "", None, ""), warmup_candles=26,
    )


def _buy_inputs(frames):
    timestamp = frames[25].candle.close_timestamp
    close = frames[25].candle.close
    response = SuppliedAIResponse(
        timestamp, "TEST",
        '{"action":"BUY","confidence":80,"reason":"historical test",'
        f'"stop_loss":{close - 2},"take_profit":{close + 4}' + "}",
    )
    intent = BacktestOrderIntent(timestamp, "bt-1", "TEST", OrderSide.BUY, Decimal("1"),
                                 OrderType.MARKET, None, None, TradingSession.REGULAR)
    return (response,), (intent,)


def test_full_pipeline_replays_and_fills_next_frame() -> None:
    frames = _frames()
    responses, intents = _buy_inputs(frames)
    result = run_backtest(frames, responses, intents, _config())
    assert result.number_of_candles == 30
    assert result.number_of_proposals == 1
    assert result.number_approved == 1
    assert result.number_filled == 1
    assert result.ending_cash < Decimal("10000")
    event_types = {event.event_type.value for event in result.checkpoint.replay_journal.events}
    assert {"INDICATORS", "STRATEGY", "PROMPT", "AI_RESPONSE", "RISK", "ORDER_COMPLIANCE", "PAPER_EXECUTION"} <= event_types


def test_identical_inputs_are_deterministic() -> None:
    frames = _frames()
    responses, intents = _buy_inputs(frames)
    assert run_backtest(frames, responses, intents, _config()) == run_backtest(frames, responses, intents, _config())


def test_pause_serialize_resume_matches_continuous_result() -> None:
    frames = _frames()
    responses, intents = _buy_inputs(frames)
    config = _config()
    uninterrupted = run_backtest(frames, responses, intents, config)
    paused = run_until(frames, responses, intents, config, 26)
    restored = checkpoint_from_json(paused.to_json())
    resumed = resume_backtest(frames, responses, intents, config, restored)
    assert resumed == uninterrupted


def test_checkpoint_rejects_changed_inputs() -> None:
    frames = _frames()
    responses, intents = _buy_inputs(frames)
    checkpoint = run_until(frames, responses, intents, _config(), 26)
    with pytest.raises(ValueError, match="fingerprint"):
        resume_backtest(frames, (), intents, _config(), checkpoint)


def test_missing_response_records_no_proposal() -> None:
    result = run_backtest(_frames(), (), (), _config())
    assert result.number_of_proposals == 0
    assert result.number_filled == 0


def test_hold_response_creates_no_order() -> None:
    frames = _frames()
    timestamp = frames[25].candle.close_timestamp
    response = SuppliedAIResponse(timestamp, "TEST", '{"action":"HOLD","confidence":80,"reason":"wait","stop_loss":null,"take_profit":null}')
    result = run_backtest(frames, (response,), (), _config())
    assert result.number_of_proposals == 0


def test_intent_action_mismatch_is_rejected() -> None:
    frames = _frames()
    responses, intents = _buy_inputs(frames)
    bad = replace(intents[0], side=OrderSide.SELL)
    result = run_backtest(frames, responses, (bad,), _config())
    assert result.number_rejected == 1 and result.number_filled == 0


def test_cash_account_sell_runs_gfv_and_paper_pipeline() -> None:
    frames = _frames()
    buy_responses, buy_intents = _buy_inputs(frames)
    timestamp = frames[26].candle.close_timestamp
    close = frames[26].candle.close
    sell_response = SuppliedAIResponse(
        timestamp, "TEST",
        '{"action":"SELL","confidence":80,"reason":"historical exit",'
        f'"stop_loss":{close + 2},"take_profit":{close - 4}' + "}",
    )
    sell_intent = BacktestOrderIntent(timestamp, "bt-2", "TEST", OrderSide.SELL, Decimal("1"),
                                      OrderType.MARKET, None, None, TradingSession.REGULAR)
    result = run_backtest(frames, (*buy_responses, sell_response), (*buy_intents, sell_intent), _config())
    assert result.number_of_proposals == 2
    assert result.number_filled == 2
    assert result.realized_pnl > 0
    assert any(event.event_type.value == "GFV" and event.status == "APPROVED"
               for event in result.checkpoint.replay_journal.events)


def test_malformed_ai_response_is_journaled_and_rejected() -> None:
    frames = _frames()
    timestamp = frames[25].candle.close_timestamp
    response = SuppliedAIResponse(timestamp, "TEST", "not-json")
    result = run_backtest(frames, (response,), (), _config())
    assert result.number_rejected == 1
    assert any(event.event_type.value == "AI_REJECTION" for event in result.checkpoint.replay_journal.events)


@pytest.mark.parametrize(
    "change",
    [
        {"high": Decimal("99")},
        {"volume": Decimal("NaN")},
        {"open_timestamp": datetime(2026, 7, 20, 14, 30)},
    ],
)
def test_malformed_historical_data_fails_closed(change: dict[str, object]) -> None:
    frames = list(_frames())
    frames[0] = replace(frames[0], candle=replace(frames[0].candle, **change))
    with pytest.raises(ValueError):
        run_backtest(tuple(frames), (), (), _config())


def test_checkpoint_corruption_is_rejected() -> None:
    with pytest.raises(ValueError, match="malformed"):
        checkpoint_from_json("{}")
