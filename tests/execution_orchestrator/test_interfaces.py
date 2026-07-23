from app.execution_orchestrator import ExecutionOrchestratorPolicy, ExecutionOrchestratorRuntime
from tests.execution_orchestrator.helpers import RuntimeSpy


def test_runtime_exposes_single_cycle_execute_boundary():
    strategy = RuntimeSpy("evaluate", lambda value: None); risk = RuntimeSpy("evaluate", lambda value: None)
    planner = RuntimeSpy("plan", lambda value: None); paper = RuntimeSpy("execute", lambda value: None)
    runtime = ExecutionOrchestratorRuntime(strategy, risk, planner, paper, ExecutionOrchestratorPolicy())
    assert callable(runtime.execute)
