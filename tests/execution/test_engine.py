from dataclasses import replace
from decimal import Decimal
import hashlib,json
import pytest
from app.committee import CommitteeAction
from app.execution import (ExecutionPolicy,ExecutionReason,ExecutionStatus,PaperExecutionEngine)
from app.risk import RiskDecisionAction
from tests.execution.helpers import STAMP,execution_request,proposal
from tests.trade_proposals.helpers import decision

def execute(policy=None,proposal_value=None):
    return PaperExecutionEngine().execute(execution_request(policy,proposal_value))

def test_long_fill_commission_slippage_and_determinism():
    policy=ExecutionPolicy(commission_per_share=Decimal(".01"),minimum_commission=Decimal(".05"),slippage_per_share=Decimal(".02"))
    first=execute(policy); second=execute(policy)
    assert first==second and first.status is ExecutionStatus.FILLED and first.reason is ExecutionReason.FILLED
    assert first.quantity==first.filled_quantity==10 and first.fill_price==Decimal("100.02")
    assert first.commission==Decimal(".10") and first.slippage==Decimal(".20")
    assert first.gross_value==Decimal("1000.20") and first.net_cost==Decimal("1000.30")
    canonical=json.dumps({"proposal_id":first.proposal_id,"timestamp":STAMP.isoformat(),"fill_price":"100.02",
      "quantity":str(first.quantity),"policy_version":policy.version,"engine_version":"paper_execution_engine_v1"},sort_keys=True,separators=(",",":"))
    assert first.execution_id==hashlib.sha256(canonical.encode()).hexdigest()
    assert [x.name for x in first.checks]==["proposal ready","quantity positive","entry positive"]

def test_short_fill_and_minimum_commission():
    short=proposal(risk_decision=decision(action=RiskDecisionAction.APPROVE,committee_action=CommitteeAction.BEARISH))
    result=execute(ExecutionPolicy(commission_per_share=Decimal(".001"),minimum_commission=Decimal("1"),slippage_per_share=Decimal(".05")),short)
    assert result.fill_price==Decimal("99.95") and result.commission==1
    assert result.gross_value==Decimal("999.5000") and result.net_cost==Decimal("998.5000")

def test_rejected_proposal_has_zero_execution_values():
    rejected=proposal(risk_decision=decision(action=RiskDecisionAction.REJECT,approved_notional=0,approved_risk_fraction=0))
    before=rejected.to_dict(); result=execute(proposal_value=rejected)
    assert result.status is ExecutionStatus.REJECTED and result.reason is ExecutionReason.PROPOSAL_NOT_READY
    assert result.filled_quantity==result.fill_price==result.commission==result.gross_value==result.net_cost==0
    assert rejected.to_dict()==before

def test_requires_execution_request():
    with pytest.raises(ValueError): PaperExecutionEngine().execute({})
