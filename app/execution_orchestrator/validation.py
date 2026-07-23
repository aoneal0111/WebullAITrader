from app.execution_orchestrator.exceptions import ExecutionOrchestratorDependencyError, ExecutionOrchestratorValidationError
from app.execution_orchestrator.models import PaperTradingCycleRequest
from app.execution_orchestrator.policies import ExecutionOrchestratorPolicy


def validate_dependencies(strategy_runtime, risk_runtime, planner_runtime, paper_runtime, policy):
    for dependency, method, name in ((strategy_runtime, "evaluate", "strategy runtime"), (risk_runtime, "evaluate", "risk runtime"),
                                     (planner_runtime, "plan", "execution planner runtime"), (paper_runtime, "execute", "paper trading runtime")):
        if dependency is None or not callable(getattr(dependency, method, None)):
            raise ExecutionOrchestratorDependencyError(f"{name} must expose {method}")
    if not isinstance(policy, ExecutionOrchestratorPolicy): raise ExecutionOrchestratorDependencyError("policy must be ExecutionOrchestratorPolicy")


def validate_request(request):
    if not isinstance(request, PaperTradingCycleRequest): raise ExecutionOrchestratorValidationError("request must be PaperTradingCycleRequest")
    return request
