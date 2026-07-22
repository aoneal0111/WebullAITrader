from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from types import MappingProxyType

import pytest

from app.committee import CommitteeAction, CommitteeOpinion, CommitteeVote
from app.risk import RiskEvaluationRequest, RiskState
from app.risk import RiskContext, RiskCriteriaResult, RiskOutcome, RiskResult, RiskRuntimeValidationError
from app.strategy import StrategySignal
from tests.risk.fixtures import context

NOW = datetime(2026, 7, 21, 12, tzinfo=UTC)

def opinion(action=CommitteeAction.BULLISH, confidence=.8, consensus=.8):
    return CommitteeOpinion("AAPL", NOW, action, confidence, .5 if action is CommitteeAction.BULLISH else 0,
        consensus, 0, 0, 0, 0, (), ("test",), (), "weights", "chair", {})

def state(**changes):
    values = dict(symbol="AAPL", timestamp=NOW, account_equity=Decimal("10000"), available_buying_power=Decimal("5000"),
        current_symbol_exposure=Decimal("100"), total_gross_exposure=Decimal("1000"), daily_realized_pnl=Decimal("0"),
        daily_unrealized_pnl=Decimal("0"), current_drawdown_fraction=Decimal("0"), open_positions=1, open_orders=1,
        metadata={"nested": [1]})
    values.update(changes); return RiskState(**values)

def test_state_is_normalized_frozen_and_json_safe():
    item = state(symbol="  AAPL  ")
    assert item.symbol == "AAPL" and item.timestamp.tzinfo is UTC
    assert isinstance(item.metadata, MappingProxyType)
    with pytest.raises((TypeError, FrozenInstanceError)): item.open_orders = 2
    json.dumps(item.to_dict(), allow_nan=False)
    assert RiskState.from_dict(item.to_dict()) == item

@pytest.mark.parametrize("changes", [
    {"symbol":"aapl"}, {"timestamp":datetime(2026,1,1)}, {"account_equity":0},
    {"available_buying_power":-1}, {"current_symbol_exposure":-1}, {"total_gross_exposure":-1},
    {"current_symbol_exposure":2,"total_gross_exposure":1}, {"current_drawdown_fraction":-1},
    {"current_drawdown_fraction":2}, {"open_positions":-1}, {"open_orders":-1},
    {"account_equity":True}, {"open_orders":False}, {"account_equity":Decimal("NaN")},
    {"account_equity":Decimal("Infinity")},
])
def test_invalid_states(changes):
    with pytest.raises(ValueError): state(**changes)

def test_request_contract():
    item = RiskEvaluationRequest(opinion(), state(), Decimal("100"), Decimal(".005"), NOW, {"x":[1]})
    assert isinstance(item.metadata, MappingProxyType)
    json.dumps(item.to_dict(), allow_nan=False)
    assert RiskEvaluationRequest.from_dict(item.to_dict()) == item

@pytest.mark.parametrize("change", [
    {"committee_opinion":object()}, {"risk_state":object()}, {"timestamp":datetime(2026,1,1)},
    {"requested_notional":-1}, {"requested_risk_fraction":-1}, {"requested_risk_fraction":2},
    {"requested_notional":True}, {"requested_risk_fraction":False},
])
def test_invalid_requests(change):
    values=dict(committee_opinion=opinion(), risk_state=state(), requested_notional=Decimal("1"), requested_risk_fraction=Decimal(".001"), timestamp=NOW)
    values.update(change)
    with pytest.raises(ValueError): RiskEvaluationRequest(**values)

def test_request_rejects_mixed_and_future_inputs():
    with pytest.raises(ValueError): RiskEvaluationRequest(opinion(), state(symbol="MSFT"), 1, 0, NOW)
    with pytest.raises(ValueError): RiskEvaluationRequest(opinion(), state(), 1, 0, NOW-timedelta(seconds=1))
    with pytest.raises(ValueError): RiskEvaluationRequest(opinion(), state(timestamp=NOW+timedelta(seconds=1)), 1, 0, NOW)

def test_neutral_requires_zero_values():
    neutral=opinion(CommitteeAction.NEUTRAL)
    with pytest.raises(ValueError): RiskEvaluationRequest(neutral,state(),1,0,NOW)
    with pytest.raises(ValueError): RiskEvaluationRequest(neutral,state(),0,.1,NOW)

def test_runtime_models_frozen_slotted_roundtrip():
    item=context(); assert not hasattr(item,"__dict__") and RiskContext.from_dict(item.to_dict())==item
    decision=item.strategy_decision; criterion=RiskCriteriaResult("quantity",True,1,2,"within limit")
    result=RiskResult(item.context_id,decision,RiskOutcome.APPROVED,1,1,(criterion,),"v1")
    assert RiskResult.from_dict(result.to_dict())==result

def test_runtime_context_signal_quantity_validation():
    with pytest.raises(RiskRuntimeValidationError): context(StrategySignal.HOLD,"1")
