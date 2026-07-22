from app.execution_orchestrator.exceptions import ExecutionOrchestratorSerializationError
from app.execution_orchestrator.models import PaperTradingCycleCriteriaResult, PaperTradingCycleRequest, PaperTradingCycleResult
from app.execution_orchestrator.policies import ExecutionOrchestratorPolicy


def _serialize(value, expected):
    if not isinstance(value, expected): raise ExecutionOrchestratorSerializationError(f"value must be {expected.__name__}")
    return value.to_dict()


serialize_request = lambda value: _serialize(value, PaperTradingCycleRequest)
serialize_result = lambda value: _serialize(value, PaperTradingCycleResult)
serialize_criteria = lambda value: _serialize(value, PaperTradingCycleCriteriaResult)
serialize_policy = lambda value: _serialize(value, ExecutionOrchestratorPolicy)
