class TradingCycleError(Exception): pass
class TradingCycleValidationError(TradingCycleError): pass
class TradingCycleDependencyError(TradingCycleError): pass
class TradingCycleEvaluationError(TradingCycleError): pass
class TradingCycleSerializationError(TradingCycleError): pass
