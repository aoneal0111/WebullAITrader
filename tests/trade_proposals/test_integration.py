from datetime import UTC, datetime
from decimal import Decimal

from app.committee import AgentOpinion, CommitteeChair, TechnicalAgent
from app.evidence.providers import TechnicalSnapshotEvidenceProvider, TechnicalSnapshotInput
from app.indicators.market_snapshot import MarketSnapshot
from app.risk import RiskCommittee, RiskEvaluationRequest, RiskState
from app.trade_proposals import TradeProposalEngine, TradeProposalPolicy, TradeProposalRequest

OBSERVED=datetime(2026,7,21,19,tzinfo=UTC)
COMMITTEE_TIME=datetime(2026,7,21,19,1,tzinfo=UTC)
RISK_TIME=datetime(2026,7,21,19,2,tzinfo=UTC)
PROPOSAL_TIME=datetime(2026,7,21,19,3,tzinfo=UTC)

def test_analysis_pipeline_ends_at_deterministic_trade_proposal():
    snapshot=MarketSnapshot("AAPL",Decimal("100"),Decimal("102"),Decimal("98"),Decimal("25"),Decimal("1"),Decimal(".5"),Decimal(".5"),Decimal("2"),Decimal("110"),Decimal("100"),Decimal("90"),Decimal("98"))
    evidence=TechnicalSnapshotEvidenceProvider().generate(TechnicalSnapshotInput(snapshot,OBSERVED))
    technical=TechnicalAgent().evaluate(evidence,timestamp=OBSERVED)
    normalized=AgentOpinion.from_technical_opinion(technical)
    opinion=CommitteeChair().evaluate((normalized,),timestamp=COMMITTEE_TIME)
    opinion_before=opinion.to_dict()
    state=RiskState("AAPL",COMMITTEE_TIME,Decimal("10000"),Decimal("1000"),Decimal("100"),Decimal("1000"),Decimal("0"),Decimal("0"),Decimal(".01"),1,0)
    risk_request=RiskEvaluationRequest(opinion,state,Decimal("500"),Decimal(".005"),RISK_TIME)
    risk=RiskCommittee().evaluate(risk_request); risk_before=risk.to_dict()
    proposal_request=TradeProposalRequest(risk,Decimal("100"),PROPOSAL_TIME,TradeProposalPolicy())
    first=TradeProposalEngine().create(proposal_request); second=TradeProposalEngine().create(proposal_request)
    assert first==second and first.proposal_id==second.proposal_id
    assert first.symbol==risk.symbol==opinion.symbol==snapshot.symbol
    assert Decimal(first.metadata["planned_notional"]) <= risk.approved_notional
    assert opinion.to_dict()==opinion_before and risk.to_dict()==risk_before
    assert not hasattr(first,"submitted_at") and not hasattr(first,"filled_quantity")
