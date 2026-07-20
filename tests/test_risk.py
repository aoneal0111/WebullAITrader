from decimal import Decimal

import pytest

from app.ai.response_models import AIResponse, ResponseAction
from app.indicators.market_snapshot import MarketSnapshot
from app.risk import evaluate_risk
from app.risk.limits import MAX_POSITION_PERCENT


def _snapshot(**overrides: object) -> MarketSnapshot:
    values: dict[str, object] = {
        "symbol": "TEST", "close": 100.0, "ema_12": 101.0, "ema_26": 99.0,
        "rsi_14": 55.0, "macd": 1.0, "macd_signal": 0.5, "macd_histogram": 0.5,
        "atr_14": 2.0, "bollinger_upper": 110.0, "bollinger_middle": 100.0,
        "bollinger_lower": 90.0, "vwap": 99.0,
    }
    values.update(overrides)
    return MarketSnapshot(**values)  # type: ignore[arg-type]


def _response(
    action: ResponseAction = ResponseAction.BUY,
    confidence: int = 80,
    stop: Decimal | None = Decimal("95"),
    target: Decimal | None = Decimal("110"),
) -> AIResponse:
    return AIResponse(action, confidence, "Deterministic test", stop, target)


def test_valid_buy_is_approved() -> None:
    result = evaluate_risk(_response(), _snapshot())
    assert result.approved and result.stop_loss_valid and result.take_profit_valid
    assert 0 < result.max_position_percent <= MAX_POSITION_PERCENT


def test_valid_sell_is_approved() -> None:
    result = evaluate_risk(
        _response(ResponseAction.SELL, stop=Decimal("105"), target=Decimal("90")), _snapshot()
    )
    assert result.approved


def test_hold_is_always_allowed_with_zero_allocation() -> None:
    result = evaluate_risk(_response(ResponseAction.HOLD, 0, None, None), _snapshot(close=float("nan")))
    assert result.approved and result.max_position_percent == 0


def test_low_confidence_is_rejected() -> None:
    result = evaluate_risk(_response(confidence=69), _snapshot())
    assert not result.approved
    assert any("Confidence" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    "response, warning",
    [
        (_response(stop=None), "Stop-loss"),
        (_response(target=None), "Take-profit"),
        (_response(stop=Decimal("99"), target=Decimal("101")), "Reward:risk"),
        (_response(stop=Decimal("100")), "BUY stop-loss"),
        (_response(target=Decimal("99")), "BUY take-profit"),
        (_response(stop=Decimal("-1")), "finite and positive"),
    ],
)
def test_invalid_limits_are_rejected(response: AIResponse, warning: str) -> None:
    result = evaluate_risk(response, _snapshot())
    assert not result.approved
    assert any(warning in item for item in result.warnings)
    assert result.max_position_percent == 0


def test_malformed_data_fails_closed() -> None:
    result = evaluate_risk(_response(), object())  # type: ignore[arg-type]
    assert not result.approved and result.risk_score == 100


def test_repeated_evaluation_is_deterministic() -> None:
    response, snapshot = _response(), _snapshot()
    assert evaluate_risk(response, snapshot) == evaluate_risk(response, snapshot)
