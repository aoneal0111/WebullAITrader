from datetime import UTC, datetime
from decimal import Decimal
from app.committee import CommitteeAction
from app.risk import RiskDecision, RiskDecisionAction, RiskLimitCheck, RiskReasonCode
from app.trade_proposals import TradeProposalPolicy, TradeProposalRequest

NOW=datetime(2026,7,21,20,tzinfo=UTC); LATER=datetime(2026,7,21,20,1,tzinfo=UTC)

def decision(**changes):
    values=dict(symbol="AAPL",timestamp=NOW,action=RiskDecisionAction.APPROVE,approved_notional=Decimal("1000"),
        approved_risk_fraction=Decimal(".01"),committee_action=CommitteeAction.BULLISH,committee_confidence=.8,
        committee_consensus=.8,primary_reason=RiskReasonCode.APPROVED,
        checks=(RiskLimitCheck(RiskReasonCode.APPROVED,True,Decimal("1"),None,"approved",True),),reasons=("approved",),
        policy_version="risk_policy_v1",committee_version="risk_committee_v1")
    values.update(changes); return RiskDecision(**values)

def request(policy=None, **changes):
    values=dict(risk_decision=decision(),reference_price=Decimal("100"),timestamp=LATER,
                policy=policy or TradeProposalPolicy())
    values.update(changes); return TradeProposalRequest(**values)
