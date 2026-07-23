from dataclasses import replace
from decimal import Decimal
import json
import pytest
from app.committee import CommitteeAction
from app.risk import RiskDecisionAction, RiskReasonCode
from app.trade_proposals import (ProposalOrderType,ProposalReasonCode,ProposalStatus,
    TradeDirection,TradeProposal,TradeProposalEngine,TradeProposalPolicy)
from tests.trade_proposals.helpers import decision,request

def create(policy=None,**changes): return TradeProposalEngine(policy).create(request(policy,**changes))

def test_requires_request():
    with pytest.raises(ValueError): TradeProposalEngine().create({})

def test_approved_market_is_ready_and_round_trips():
    result=create(); assert result.status is ProposalStatus.READY and result.direction is TradeDirection.LONG
    assert result.proposed_entry_price==100 and result.quantity==10 and result.metadata["planned_notional"]=="1000"
    assert result.stop_loss_price==98 and result.take_profit_price==104
    assert result.per_unit_risk==2 and result.total_planned_risk==20 and result.expected_reward==40 and result.risk_reward_ratio==2
    assert TradeProposal.from_dict(result.to_dict())==result; json.dumps(result.to_dict(),allow_nan=False)

def test_modify_and_short_are_ready():
    risk=decision(action=RiskDecisionAction.MODIFY,approved_notional=Decimal("500"),primary_reason=RiskReasonCode.REQUESTED_RISK_LIMIT,
                  committee_action=CommitteeAction.BEARISH)
    result=create(risk_decision=risk); assert result.status is ProposalStatus.READY and result.direction is TradeDirection.SHORT
    assert result.stop_loss_price==102 and result.take_profit_price==96

@pytest.mark.parametrize("risk,code",[
    (decision(action=RiskDecisionAction.REJECT,approved_notional=0,approved_risk_fraction=0),ProposalReasonCode.RISK_NOT_APPROVED),
    (decision(approved_notional=0),ProposalReasonCode.ZERO_APPROVED_NOTIONAL),
    (decision(committee_action=CommitteeAction.NEUTRAL),ProposalReasonCode.NON_DIRECTIONAL_COMMITTEE),
])
def test_authorization_rejections(risk,code):
    result=create(risk_decision=risk); assert result.status is ProposalStatus.REJECTED and result.primary_reason is code
    assert result.approved_notional==result.quantity==result.total_planned_risk==0 and len(result.checks)==13

def test_long_and_short_limit_offsets_round_conservatively():
    policy=TradeProposalPolicy(order_type=ProposalOrderType.LIMIT,limit_price_offset_fraction=Decimal(".015"))
    assert create(policy).proposed_entry_price==Decimal("98.50")
    risk=decision(committee_action=CommitteeAction.BEARISH)
    assert create(policy,risk_decision=risk).proposed_entry_price==Decimal("101.50")

def test_quantity_rounding_fractional_whole_increment_and_cap():
    fractional=create(reference_price=Decimal("300")); assert fractional.quantity==Decimal("3.3333")
    whole=TradeProposalPolicy(allow_fractional_quantity=False); assert create(whole,reference_price=Decimal("300")).quantity==3
    increment=TradeProposalPolicy(quantity_increment=Decimal(".25")); assert create(increment,reference_price=Decimal("300")).quantity==Decimal("3.25")
    capped=TradeProposalPolicy(maximum_quantity=Decimal("2.5")); assert create(capped).quantity==Decimal("2.5")
    for result in (fractional,create(whole,reference_price=Decimal("300")),create(increment,reference_price=Decimal("300")),create(capped)):
        assert Decimal(result.metadata["planned_notional"]) <= Decimal(result.metadata["authorized_notional"])

@pytest.mark.parametrize("policy,code",[
    (TradeProposalPolicy(minimum_quantity=Decimal("20")),ProposalReasonCode.QUANTITY_BELOW_MINIMUM),
    (TradeProposalPolicy(minimum_notional=Decimal("2000")),ProposalReasonCode.NOTIONAL_BELOW_MINIMUM),
    (TradeProposalPolicy(minimum_risk_reward_ratio=Decimal("2.01")),ProposalReasonCode.RISK_REWARD_TOO_LOW),
])
def test_constraints_reject(policy,code):
    result=create(policy); assert result.status is ProposalStatus.REJECTED
    assert any(not x.passed and x.code is code for x in result.checks)

def test_invalid_stop_and_target_distances_reject():
    stop=create(TradeProposalPolicy(price_increment=Decimal("1")),reference_price=Decimal(".5"))
    assert any(not x.passed and x.code is ProposalReasonCode.STOP_DISTANCE_TOO_LARGE for x in stop.checks)
    short=decision(committee_action=CommitteeAction.BEARISH)
    target=create(TradeProposalPolicy(take_profit_fraction=Decimal("1.01")),risk_decision=short)
    assert any(not x.passed and x.code is ProposalReasonCode.TARGET_DISTANCE_INVALID for x in target.checks)

def test_exact_ratio_passes_and_multiple_failures_are_stable():
    assert create(TradeProposalPolicy(minimum_risk_reward_ratio=Decimal("2"))).status is ProposalStatus.READY
    policy=TradeProposalPolicy(minimum_quantity=20,minimum_notional=2000,minimum_risk_reward_ratio=3)
    first=create(policy); second=create(policy)
    assert first==second and first.primary_reason is ProposalReasonCode.MULTIPLE_CONSTRAINTS
    assert first.proposal_id==second.proposal_id and [x.code for x in first.checks]==[x.code for x in second.checks]

def test_material_input_changes_id_and_risk_is_unchanged():
    risk=decision(); before=risk.to_dict(); first=create(risk_decision=risk); second=create(risk_decision=risk,reference_price=Decimal("101"))
    assert first.proposal_id!=second.proposal_id and risk.to_dict()==before
    assert not hasattr(first,"broker_order_id") and not hasattr(first,"execution_status")
