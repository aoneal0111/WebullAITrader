class ExecutionOrchestratorError(Exception):
    """Base error for deterministic paper execution orchestration."""


class ExecutionOrchestratorValidationError(ExecutionOrchestratorError): pass
class ExecutionOrchestratorDependencyError(ExecutionOrchestratorError): pass
class ExecutionOrchestratorSerializationError(ExecutionOrchestratorError): pass


class ExecutionOrchestratorStageError(ExecutionOrchestratorError):
    def __init__(self, stage, message):
        self.stage = stage
        super().__init__(message)
