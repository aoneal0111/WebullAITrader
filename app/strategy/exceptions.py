class StrategyError(Exception):
    """Base error for the broker-neutral strategy runtime."""
class StrategyValidationError(StrategyError): pass
class StrategyDependencyError(StrategyError): pass
class StrategyEvaluationError(StrategyError): pass
class StrategySerializationError(StrategyError): pass
