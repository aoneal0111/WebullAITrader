import pytest
from app.strategy import *
from tests.strategy.fixtures import context
def test_serializers():
 decision=StrategyDecision("A",StrategySignal.BUY,"0.8",("synthetic",));result=StrategyResult("c",True,(decision,),"v");assert serialize_context(context())==context().to_dict() and serialize_decision(decision)==decision.to_dict() and serialize_result(result)==result.to_dict() and serialize_policy(StrategyPolicy())==StrategyPolicy().to_dict()
def test_wrong_type():
 with pytest.raises(StrategySerializationError):serialize_context(object())
