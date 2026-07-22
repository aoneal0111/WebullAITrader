from inspect import signature
from app.execution_planner import *
def test_exact_interfaces():
 assert {n for n in dir(DeterministicExecutionPlannerRuntime) if not n.startswith("_")}=={"plan"}
 assert list(signature(ExecutionPlannerRuntime.plan).parameters)==["self","request"] and list(signature(ExecutionPlannerEvaluator.evaluate).parameters)==["self","request","policy"]
