import json
from datetime import UTC, datetime

import pytest

from app.ai.prompt_builder import PromptValidationError, build_prompt_package
from app.indicators.market_snapshot import MarketSnapshot
from app.strategy.scoring import analyze_snapshot, score_snapshot


def _snapshot(**overrides: object) -> MarketSnapshot:
    values: dict[str, object] = {
        "symbol": "TEST",
        "close": 110.0,
        "ema_12": 105.0,
        "ema_26": 100.0,
        "rsi_14": 60.0,
        "macd": 2.0,
        "macd_signal": 1.0,
        "macd_histogram": 1.0,
        "atr_14": 2.0,
        "bollinger_upper": 115.0,
        "bollinger_middle": 105.0,
        "bollinger_lower": 95.0,
        "vwap": 104.0,
    }
    values.update(overrides)
    return MarketSnapshot(**values)  # type: ignore[arg-type]


def test_builds_complete_prompt_package() -> None:
    snapshot = _snapshot()
    score = score_snapshot(snapshot)
    package = build_prompt_package(
        snapshot,
        analyze_snapshot(snapshot),
        score,
        timestamp=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )
    user_data = json.loads(package.user_prompt)
    serialized = json.loads(package.to_json())
    assert user_data["symbol"] == "TEST"
    assert user_data["current_price"] == "110.0"
    assert user_data["indicators"]["vwap"] == "104.0"
    assert user_data["strategy"]["overall_score"] == analyze_snapshot(snapshot).overall_score
    assert serialized["metadata"] == {
        "prompt_version": "1.0",
        "strategy_version": "1.0",
        "symbol": "TEST",
        "timestamp": "2026-07-18T12:00:00+00:00",
    }


def test_system_prompt_enforces_safe_json_contract() -> None:
    snapshot = _snapshot()
    package = build_prompt_package(snapshot, analyze_snapshot(snapshot), score_snapshot(snapshot))
    prompt = package.system_prompt
    assert "Never invent" in prompt
    assert "Return JSON only" in prompt
    assert "BUY, SELL, or HOLD" in prompt
    assert "insufficient" in prompt
    assert "uncertainty" in prompt


def test_unavailable_indicators_are_serialized_as_null() -> None:
    snapshot = _snapshot(rsi_14=None, atr_14=None, vwap=None)
    package = build_prompt_package(snapshot, analyze_snapshot(snapshot), score_snapshot(snapshot))
    indicators = json.loads(package.user_prompt)["indicators"]
    assert indicators["rsi_14"] is None
    assert indicators["atr_14"] is None
    assert indicators["vwap"] is None


def test_invalid_price_is_rejected() -> None:
    snapshot = _snapshot(close=0.0)
    with pytest.raises(PromptValidationError, match="current price"):
        build_prompt_package(snapshot, analyze_snapshot(snapshot), score_snapshot(snapshot))


def test_naive_timestamp_is_rejected() -> None:
    snapshot = _snapshot()
    with pytest.raises(PromptValidationError, match="timezone"):
        build_prompt_package(
            snapshot,
            analyze_snapshot(snapshot),
            score_snapshot(snapshot),
            timestamp=datetime(2026, 7, 18),
        )
