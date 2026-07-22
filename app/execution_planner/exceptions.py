class ExecutionPlannerError(Exception):
    """Base error for deterministic execution planning."""
class ExecutionPlannerValidationError(ExecutionPlannerError): pass
class ExecutionPlannerDependencyError(ExecutionPlannerError): pass
class ExecutionPlannerEvaluationError(ExecutionPlannerError): pass
class ExecutionPlannerSerializationError(ExecutionPlannerError): pass
