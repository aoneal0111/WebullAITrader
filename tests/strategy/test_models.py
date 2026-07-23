from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest
from app.strategy import *
from tests.strategy.fixtures import context
def test_context_frozen_slotted_roundtrip_and_nested_immutability():
 value=context();assert not hasattr(value,"__dict__") and StrategyContext.from_dict(value.to_dict())==value
 with pytest.raises(FrozenInstanceError):value.context_id="x"
 with pytest.raises(TypeError):value.configuration["x"]=1
def test_all_broker_neutral_signals_and_decision_roundtrip():
 for signal in StrategySignal:
  value=StrategyDecision("aapl",signal,"0.5",("synthetic",));assert value.symbol=="AAPL" and isinstance(value.confidence,Decimal) and StrategyDecision.from_dict(value.to_dict())==value
def test_decision_validation():
 with pytest.raises(StrategyValidationError):StrategyDecision("A",StrategySignal.BUY,"1.1",("bad",))
 with pytest.raises(StrategyValidationError):StrategyDecision("A",StrategySignal.BUY,"0.5",())
def test_result_roundtrip_unique_symbols():
 d=StrategyDecision("A",StrategySignal.HOLD,"0.5",("hold",));r=StrategyResult("c",True,(d,),"v1");assert StrategyResult.from_dict(r.to_dict())==r
 with pytest.raises(StrategyValidationError):StrategyResult("c",True,(d,d),"v1")
 with pytest.raises(StrategyValidationError):StrategyResult("c",False,(d,),"v1")
