from __future__ import annotations

from types import SimpleNamespace

from app.paper_gateway.gateway import _decision_reasoning, _decision_strategy_id


def test_autonomous_warrior_lifecycle_keeps_strategy_attribution() -> None:
    order = SimpleNamespace(
        strategy_lifecycle_id="WARRIOR_MOMENTUM_V1|MSS|EPISODE|2.152005",
    )
    assert _decision_strategy_id(order) == "WARRIOR_MOMENTUM_V1"
    assert "autonomous" in _decision_reasoning(order).lower()


def test_operator_order_keeps_operator_attribution() -> None:
    order = SimpleNamespace(strategy_lifecycle_id=None)
    assert _decision_strategy_id(order) == "operator-order-entry"
    assert _decision_reasoning(order).startswith("Operator submitted")
