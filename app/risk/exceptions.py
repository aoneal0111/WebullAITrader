class RiskRuntimeError(Exception):
    """Base error for the broker-neutral risk runtime."""
class RiskRuntimeValidationError(RiskRuntimeError): pass
class RiskRuntimeDependencyError(RiskRuntimeError): pass
class RiskRuntimeEvaluationError(RiskRuntimeError): pass
class RiskRuntimeSerializationError(RiskRuntimeError): pass
