from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.order_compliance.models import (
    OrderSide,
    OrderType,
    TradingSession,
)
from app.strategy.scoring import StrategyAction, StrategyScore
from app.strategy.volatility import VolatilityRegime
from app.strategy_engine import (
    StrategyDecisionAction,
    StrategyEngine,
    StrategyEngineConfig,
    StrategyPosition,
    create_order_intent,
)

NOW = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)


@dataclass(frozen=True)
class FakeSnapshot:
    symbol: str


def score(
    action: StrategyAction,
    confidence: int,
    value: str,
) -> StrategyScore:
    decimal_value = Decimal(value)

    return StrategyScore(
        action=action,
        confidence=confidence,
        score=decimal_value,
        trend_score=decimal_value,
        momentum_score=decimal_value,
        volatility_score=Decimal("1"),
        volatility_regime=VolatilityRegime.NORMAL,
        reasons=("trend", "momentum", "volatility"),
    )


def engine_for(result: StrategyScore, **config):
    return StrategyEngine(
        StrategyEngineConfig(**config),
        scorer=lambda snapshot: result,
    )


def test_flat_buy_signal_enters_long() -> None:
    engine = engine_for(
        score(StrategyAction.BUY, 80, "0.8")
    )

    decision = engine.evaluate(
        FakeSnapshot("aapl"),
        StrategyPosition("AAPL"),
        timestamp=NOW,
    )

    assert decision.symbol == "AAPL"
    assert decision.action is (
        StrategyDecisionAction.ENTER_LONG
    )
    assert decision.creates_order_intent is True


def test_flat_weak_buy_is_ignored() -> None:
    engine = engine_for(
        score(StrategyAction.BUY, 59, "0.4")
    )

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW,
    )

    assert decision.action is StrategyDecisionAction.IGNORE


def test_flat_hold_signal_holds() -> None:
    engine = engine_for(
        score(StrategyAction.HOLD, 90, "0")
    )

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW,
    )

    assert decision.action is StrategyDecisionAction.HOLD


def test_short_entry_disabled_by_default() -> None:
    engine = engine_for(
        score(StrategyAction.SELL, 90, "-0.9")
    )

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW,
    )

    assert decision.action is StrategyDecisionAction.IGNORE


def test_short_entry_can_be_enabled() -> None:
    engine = engine_for(
        score(StrategyAction.SELL, 90, "-0.9"),
        allow_short_entries=True,
    )

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW,
    )

    assert decision.action is (
        StrategyDecisionAction.ENTER_SHORT
    )


def test_sell_signal_exits_long() -> None:
    engine = engine_for(
        score(StrategyAction.SELL, 70, "-0.7")
    )

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL", Decimal("10")),
        timestamp=NOW,
    )

    assert decision.action is StrategyDecisionAction.EXIT_LONG


def test_weak_sell_does_not_exit_long() -> None:
    engine = engine_for(
        score(StrategyAction.SELL, 49, "-0.3")
    )

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL", Decimal("10")),
        timestamp=NOW,
    )

    assert decision.action is StrategyDecisionAction.HOLD


def test_buy_signal_exits_short() -> None:
    engine = engine_for(
        score(StrategyAction.BUY, 70, "0.7")
    )

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL", Decimal("-10")),
        timestamp=NOW,
    )

    assert decision.action is StrategyDecisionAction.EXIT_SHORT


def test_snapshot_and_position_must_match() -> None:
    engine = engine_for(
        score(StrategyAction.BUY, 80, "0.8")
    )

    with pytest.raises(ValueError, match="must match"):
        engine.evaluate(
            FakeSnapshot("AAPL"),
            StrategyPosition("MSFT"),
            timestamp=NOW,
        )


def test_timestamp_must_be_aware() -> None:
    engine = engine_for(
        score(StrategyAction.BUY, 80, "0.8")
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        engine.evaluate(
            FakeSnapshot("AAPL"),
            StrategyPosition("AAPL"),
            timestamp=datetime(2026, 7, 20),
        )


def test_cooldown_ignores_repeated_action() -> None:
    engine = engine_for(
        score(StrategyAction.BUY, 80, "0.8"),
        cooldown_seconds=60,
    )

    first = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW,
    )
    second = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW + timedelta(seconds=30),
    )

    assert first.action is StrategyDecisionAction.ENTER_LONG
    assert second.action is StrategyDecisionAction.IGNORE


def test_cooldown_expires() -> None:
    engine = engine_for(
        score(StrategyAction.BUY, 80, "0.8"),
        cooldown_seconds=60,
    )

    engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW,
    )

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW + timedelta(seconds=60),
    )

    assert decision.action is (
        StrategyDecisionAction.ENTER_LONG
    )


def test_reset_cooldown_for_symbol() -> None:
    engine = engine_for(
        score(StrategyAction.BUY, 80, "0.8"),
        cooldown_seconds=60,
    )

    engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW,
    )
    engine.reset_cooldown("AAPL")

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW + timedelta(seconds=1),
    )

    assert decision.action is (
        StrategyDecisionAction.ENTER_LONG
    )


def test_evaluate_many_uses_flat_default_position() -> None:
    engine = engine_for(
        score(StrategyAction.BUY, 80, "0.8")
    )

    decisions = engine.evaluate_many(
        (
            FakeSnapshot("AAPL"),
            FakeSnapshot("MSFT"),
        ),
        {},
        timestamp=NOW,
    )

    assert len(decisions) == 2
    assert all(
        item.action is StrategyDecisionAction.ENTER_LONG
        for item in decisions
    )


def test_entry_decision_creates_buy_intent() -> None:
    engine = engine_for(
        score(StrategyAction.BUY, 80, "0.8")
    )

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW,
    )

    intent = create_order_intent(
        decision,
        quantity=Decimal("5"),
        request_id="strategy-1",
    )

    assert intent.side is OrderSide.BUY
    assert intent.quantity == Decimal("5")
    assert intent.order_type is OrderType.MARKET
    assert intent.requested_session is TradingSession.REGULAR


def test_exit_long_creates_sell_intent() -> None:
    engine = engine_for(
        score(StrategyAction.SELL, 80, "-0.8")
    )

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL", Decimal("5")),
        timestamp=NOW,
    )

    intent = create_order_intent(
        decision,
        quantity=Decimal("5"),
        request_id="strategy-exit-1",
    )

    assert intent.side is OrderSide.SELL


def test_hold_cannot_create_order_intent() -> None:
    engine = engine_for(
        score(StrategyAction.HOLD, 80, "0")
    )

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW,
    )

    with pytest.raises(
        ValueError,
        match="does not create",
    ):
        create_order_intent(
            decision,
            quantity=Decimal("5"),
            request_id="invalid",
        )


def test_intent_quantity_must_be_explicit_and_positive() -> None:
    engine = engine_for(
        score(StrategyAction.BUY, 80, "0.8")
    )

    decision = engine.evaluate(
        FakeSnapshot("AAPL"),
        StrategyPosition("AAPL"),
        timestamp=NOW,
    )

    with pytest.raises(
        ValueError,
        match="quantity must be positive",
    ):
        create_order_intent(
            decision,
            quantity=Decimal("0"),
            request_id="strategy-1",
        )


def test_config_validation() -> None:
    with pytest.raises(
        ValueError,
        match="entry_confidence",
    ):
        StrategyEngineConfig(entry_confidence=101)
