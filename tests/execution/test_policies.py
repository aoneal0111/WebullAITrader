from dataclasses import FrozenInstanceError
from decimal import Decimal
import json
from types import MappingProxyType
import pytest
from app.execution import ExecutionPolicy

def test_policy_defaults_immutable_and_round_trip():
    policy=ExecutionPolicy(metadata={"nested":[1]})
    assert isinstance(policy.metadata,MappingProxyType) and policy.version=="execution_policy_v1"
    assert ExecutionPolicy.from_dict(policy.to_dict())==policy
    json.dumps(policy.to_dict(),allow_nan=False)
    with pytest.raises(FrozenInstanceError): policy.minimum_commission=Decimal("1")

@pytest.mark.parametrize("name",["commission_per_share","minimum_commission","slippage_per_share"])
def test_policy_rejects_negative_nonfinite_and_boolean(name):
    for value in (-1,Decimal("NaN"),Decimal("Infinity"),True):
        with pytest.raises(ValueError): ExecutionPolicy(**{name:value})

def test_policy_validates_other_fields():
    with pytest.raises(ValueError): ExecutionPolicy(version=" ")
    with pytest.raises(ValueError): ExecutionPolicy(allow_partial_fills=1)
