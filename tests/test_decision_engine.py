import pytest

from app.ai.decision_engine import DecisionValidationError, TradingDecision


def test_decision_matches_requested_output_shape() -> None:
    decision = TradingDecision.from_mapping(
        {
            "action": "BUY",
            "confidence": 94,
            "reason": "Test rationale",
            "stop_loss": 123.45,
            "take_profit": 130.25,
        }
    )

    assert decision.to_dict() == {
        "action": "BUY",
        "confidence": 94,
        "reason": "Test rationale",
        "stop_loss": "123.45",
        "take_profit": "130.25",
    }


@pytest.mark.parametrize("confidence", [-1, 101])
def test_confidence_outside_percentage_range_is_rejected(confidence: int) -> None:
    with pytest.raises(DecisionValidationError, match="confidence"):
        TradingDecision.from_mapping(
            {
                "action": "HOLD",
                "confidence": confidence,
                "reason": "Test rationale",
                "stop_loss": None,
                "take_profit": None,
            }
        )


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(DecisionValidationError, match="invalid action"):
        TradingDecision.from_mapping(
            {"action": "EXECUTE", "confidence": 90, "reason": "No", "stop_loss": None, "take_profit": None}
        )
