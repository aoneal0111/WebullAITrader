from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
from decimal import Decimal
import json
from types import MappingProxyType
import pytest
from app.risk import LegacyRiskDecision, RiskDecisionAction
from app.trade_proposals import TradeProposalRequest
from tests.trade_proposals.helpers import LATER, NOW, decision, request

def test_request_frozen_immutable_round_trip():
    item=request(metadata={"x":[1]}); assert isinstance(item.metadata,MappingProxyType)
    assert TradeProposalRequest.from_dict(item.to_dict())==item; json.dumps(item.to_dict(),allow_nan=False)
    with pytest.raises(FrozenInstanceError): item.reference_price=Decimal("2")

@pytest.mark.parametrize("changes",[
    {"risk_decision":object()},{"risk_decision":LegacyRiskDecision(True,"x",1,1,True,True,())},
    {"timestamp":datetime(2026,1,1)},{"timestamp":NOW-timedelta(seconds=1)},
    {"reference_price":0},{"reference_price":-1},{"reference_price":True},
    {"reference_price":Decimal("NaN")},{"reference_price":Decimal("Infinity")},{"policy":object()},
])
def test_invalid_request(changes):
    with pytest.raises(ValueError): request(**changes)

def test_rejected_risk_decision_is_valid_input():
    rejected=decision(action=RiskDecisionAction.REJECT,approved_notional=0,approved_risk_fraction=0)
    assert request(risk_decision=rejected).risk_decision is rejected
