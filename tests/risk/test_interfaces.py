from inspect import signature
from app.risk import *
def test_runtime_interfaces():
 assert {n for n in dir(DeterministicRiskRuntime) if not n.startswith("_")}=={"evaluate"}
 assert list(signature(RiskRuntime.evaluate).parameters)==["self","context"] and list(signature(RiskEvaluator.evaluate).parameters)==["self","context","policy"]
