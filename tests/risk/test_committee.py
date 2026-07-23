from dataclasses import replace
from decimal import Decimal
import json
import pytest
from app.committee import CommitteeAction
from app.risk import RiskCommittee, RiskDecision, RiskDecisionAction, RiskEvaluationRequest, RiskPolicy, RiskReasonCode
from tests.risk.test_models import NOW, opinion, state

def request(**changes):
    values=dict(committee_opinion=opinion(),risk_state=state(),requested_notional=Decimal("500"),requested_risk_fraction=Decimal(".005"),timestamp=NOW)
    values.update(changes); return RiskEvaluationRequest(**values)

def evaluate(policy=None, **changes): return RiskCommittee(policy).evaluate(request(**changes))

def test_full_request_approves_and_serializes():
    result=evaluate(); assert result.action is RiskDecisionAction.APPROVE
    assert result.approved_notional==500 and result.approved_risk_fraction==Decimal(".005")
    assert len(result.checks)==12 and result.primary_reason is RiskReasonCode.APPROVED
    json.dumps(result.to_dict(),allow_nan=False); assert RiskDecision.from_dict(result.to_dict())==result

def test_input_type_required():
    with pytest.raises(ValueError): RiskCommittee().evaluate({})

def test_neutral_and_zero_directional_reject():
    neutral=request(committee_opinion=opinion(CommitteeAction.NEUTRAL),requested_notional=0,requested_risk_fraction=0)
    assert RiskCommittee().evaluate(neutral).primary_reason is RiskReasonCode.NEUTRAL_COMMITTEE
    assert evaluate(requested_notional=0).primary_reason is RiskReasonCode.ZERO_REQUESTED_NOTIONAL

@pytest.mark.parametrize("field,low,exact,code", [
    ("confidence",.19,.20,RiskReasonCode.COMMITTEE_CONFIDENCE_TOO_LOW),
    ("consensus",.49,.50,RiskReasonCode.COMMITTEE_CONSENSUS_TOO_LOW),
])
def test_committee_quality_boundaries(field,low,exact,code):
    kwargs={field:low}; assert evaluate(committee_opinion=opinion(**kwargs)).primary_reason is code
    kwargs[field]=exact; assert evaluate(committee_opinion=opinion(**kwargs)).action is RiskDecisionAction.APPROVE

def test_requested_risk_modifies_proportionally_or_rejects():
    result=evaluate(requested_notional=Decimal("1000"),requested_risk_fraction=Decimal(".02"))
    assert result.action is RiskDecisionAction.MODIFY and result.approved_notional==500 and result.approved_risk_fraction==Decimal(".01")
    result=evaluate(RiskPolicy(allow_modification=False),requested_risk_fraction=Decimal(".02"))
    assert result.action is RiskDecisionAction.REJECT

@pytest.mark.parametrize("risk_changes,code", [
    ({"daily_realized_pnl":Decimal("-300")},RiskReasonCode.DAILY_LOSS_LIMIT),
    ({"current_drawdown_fraction":Decimal(".10")},RiskReasonCode.DRAWDOWN_LIMIT),
    ({"open_positions":10},RiskReasonCode.OPEN_POSITION_LIMIT),
    ({"open_orders":10},RiskReasonCode.OPEN_ORDER_LIMIT),
    ({"available_buying_power":0},RiskReasonCode.INSUFFICIENT_BUYING_POWER),
    ({"current_symbol_exposure":Decimal("1000")},RiskReasonCode.SYMBOL_EXPOSURE_LIMIT),
    ({"total_gross_exposure":Decimal("5000")},RiskReasonCode.GROSS_EXPOSURE_LIMIT),
])
def test_absolute_boundaries_reject(risk_changes,code):
    if "current_symbol_exposure" in risk_changes: risk_changes["total_gross_exposure"]=risk_changes["current_symbol_exposure"]
    result=evaluate(risk_state=state(**risk_changes)); assert result.action is RiskDecisionAction.REJECT and result.primary_reason is code
    assert result.approved_notional==0 and len(result.checks)==12

@pytest.mark.parametrize("risk_changes,expected,code", [
    ({"available_buying_power":Decimal("300")},Decimal("300"),RiskReasonCode.INSUFFICIENT_BUYING_POWER),
    ({"current_symbol_exposure":Decimal("800")},Decimal("200"),RiskReasonCode.SYMBOL_EXPOSURE_LIMIT),
    ({"total_gross_exposure":Decimal("4700")},Decimal("300"),RiskReasonCode.GROSS_EXPOSURE_LIMIT),
])
def test_partial_capacity_modifies(risk_changes,expected,code):
    result=evaluate(risk_state=state(**risk_changes)); assert result.action is RiskDecisionAction.MODIFY
    assert result.approved_notional==expected and result.primary_reason is code

def test_multiple_limits_and_order_and_repeatability():
    item=request(committee_opinion=opinion(confidence=.1,consensus=.1),risk_state=state(open_positions=10,open_orders=10))
    first=RiskCommittee().evaluate(item); second=RiskCommittee().evaluate(item)
    assert first==second and first.primary_reason is RiskReasonCode.MULTIPLE_LIMITS
    assert [x.code for x in first.checks]==[RiskReasonCode.NEUTRAL_COMMITTEE,RiskReasonCode.ZERO_REQUESTED_NOTIONAL,RiskReasonCode.COMMITTEE_CONFIDENCE_TOO_LOW,RiskReasonCode.COMMITTEE_CONSENSUS_TOO_LOW,RiskReasonCode.REQUESTED_RISK_LIMIT,RiskReasonCode.DAILY_LOSS_LIMIT,RiskReasonCode.DRAWDOWN_LIMIT,RiskReasonCode.OPEN_POSITION_LIMIT,RiskReasonCode.OPEN_ORDER_LIMIT,RiskReasonCode.INSUFFICIENT_BUYING_POWER,RiskReasonCode.SYMBOL_EXPOSURE_LIMIT,RiskReasonCode.GROSS_EXPOSURE_LIMIT]

def test_zero_requested_risk_does_not_divide(): assert evaluate(requested_risk_fraction=0).action is RiskDecisionAction.APPROVE
