from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from types import MappingProxyType
import pytest
from app.risk import RiskPolicy

def test_defaults_are_frozen():
    policy=RiskPolicy(metadata={"x":[1]}); assert policy.version=="risk_policy_v1"
    assert isinstance(policy.metadata, MappingProxyType)
    with pytest.raises(FrozenInstanceError): policy.version="x"
    assert RiskPolicy.from_dict(policy.to_dict()) == policy

@pytest.mark.parametrize("name,value", [
    ("maximum_symbol_exposure_fraction",-1),("maximum_gross_exposure_fraction",2),
    ("maximum_daily_loss_fraction",Decimal("NaN")),("maximum_drawdown_fraction",Decimal("Infinity")),
    ("maximum_requested_risk_fraction",True),("minimum_committee_confidence",False),
    ("maximum_open_positions",-1),("maximum_open_orders",False),
])
def test_invalid_policy_values(name,value):
    with pytest.raises(ValueError): replace(RiskPolicy(), **{name:value})

def test_fraction_boundaries_are_valid():
    assert replace(RiskPolicy(), maximum_drawdown_fraction=0).maximum_drawdown_fraction == 0
    assert replace(RiskPolicy(), maximum_drawdown_fraction=1).maximum_drawdown_fraction == 1
