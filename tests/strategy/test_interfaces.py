from inspect import signature
from app.strategy import *
def test_exact_interfaces():
 assert {n for n in dir(DeterministicStrategyRuntime) if not n.startswith("_")}=={"evaluate"}
 assert list(signature(StrategyEvaluator.evaluate).parameters)==["self","context"] and list(signature(StrategyRuntime.evaluate).parameters)==["self","context"]
