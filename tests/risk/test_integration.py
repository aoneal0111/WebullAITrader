from datetime import UTC, datetime
from decimal import Decimal

from app.committee import AgentOpinion, CommitteeChair, TechnicalAgent
from app.evidence.providers import TechnicalSnapshotEvidenceProvider, TechnicalSnapshotInput
from app.indicators.market_snapshot import MarketSnapshot
from app.risk import RiskCommittee, RiskEvaluationRequest, RiskState

OBSERVED=datetime(2026,7,21,19,tzinfo=UTC); COMMITTEE_TIME=datetime(2026,7,21,19,1,tzinfo=UTC); REQUEST_TIME=datetime(2026,7,21,19,2,tzinfo=UTC)

def test_analysis_only_pipeline_reaches_deterministic_risk_decision():
    snapshot=MarketSnapshot("AAPL",Decimal("100"),Decimal("102"),Decimal("98"),Decimal("25"),Decimal("1"),Decimal(".5"),Decimal(".5"),Decimal("2"),Decimal("110"),Decimal("100"),Decimal("90"),Decimal("98"))
    evidence=TechnicalSnapshotEvidenceProvider().generate(TechnicalSnapshotInput(snapshot,OBSERVED))
    technical=TechnicalAgent().evaluate(evidence,timestamp=OBSERVED)
    normalized=AgentOpinion.from_technical_opinion(technical)
    opinion=CommitteeChair().evaluate((normalized,),timestamp=COMMITTEE_TIME)
    original=opinion.to_dict()
    state=RiskState("AAPL",COMMITTEE_TIME,Decimal("10000"),Decimal("1000"),Decimal("100"),Decimal("1000"),Decimal("0"),Decimal("0"),Decimal(".01"),1,0)
    request=RiskEvaluationRequest(opinion,state,Decimal("500"),Decimal(".005"),REQUEST_TIME)
    first=RiskCommittee().evaluate(request); second=RiskCommittee().evaluate(request)
    assert first==second and first.symbol==snapshot.symbol==opinion.symbol
    assert first.approved_notional==Decimal("500") and opinion.to_dict()==original
    assert not hasattr(first,"quantity") and not hasattr(first,"order_type") and not hasattr(first,"side")
from app.risk import DeterministicRiskEvaluator,DeterministicRiskRuntime,RiskOutcome
from tests.risk.fixtures import context,enabled_policy

def test_strategy_portfolio_risk_runtime_integration():
    result=DeterministicRiskRuntime(DeterministicRiskEvaluator(),enabled_policy()).evaluate(context())
    assert result.outcome is RiskOutcome.APPROVED and result.strategy_decision.symbol=="AAPL"
