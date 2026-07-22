class BacktestSuiteError(Exception): pass
class BacktestSuiteValidationError(BacktestSuiteError): pass
class BacktestSuiteDependencyError(BacktestSuiteError): pass
class BacktestSuiteResultError(BacktestSuiteError): pass
class BacktestSuiteSerializationError(BacktestSuiteError): pass
