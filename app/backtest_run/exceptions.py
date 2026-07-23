class BacktestRunError(Exception): pass
class BacktestRunValidationError(BacktestRunError): pass
class BacktestRunDependencyError(BacktestRunError): pass
class BacktestRunFactoryError(BacktestRunError): pass
class BacktestRunResultError(BacktestRunError): pass
class BacktestRunSerializationError(BacktestRunError): pass
