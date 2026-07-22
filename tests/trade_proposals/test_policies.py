from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import json
from types import MappingProxyType
import pytest
from app.trade_proposals import ProposalOrderType, TradeProposalPolicy

def test_defaults_frozen_and_round_trip():
    item=TradeProposalPolicy(metadata={"x":[1]}); assert item.order_type is ProposalOrderType.MARKET
    assert isinstance(item.metadata,MappingProxyType); json.dumps(item.to_dict(),allow_nan=False)
    assert TradeProposalPolicy.from_dict(item.to_dict())==item
    with pytest.raises(FrozenInstanceError): item.version="x"

@pytest.mark.parametrize("changes",[
    {"version":""},{"order_type":"MARKET"},{"limit_price_offset_fraction":-1},{"stop_loss_fraction":0},
    {"stop_loss_fraction":1},{"take_profit_fraction":0},{"minimum_risk_reward_ratio":0},
    {"minimum_notional":-1},{"minimum_quantity":0},{"quantity_increment":0},{"price_increment":0},
    {"maximum_quantity":0},{"stop_loss_fraction":True},{"minimum_notional":False},
    {"take_profit_fraction":Decimal("NaN")},{"price_increment":Decimal("Infinity")},
])
def test_invalid_policy(changes):
    with pytest.raises(ValueError): replace(TradeProposalPolicy(),**changes)
