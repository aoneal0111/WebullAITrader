class BacktestReportError(Exception): pass
class BacktestReportValidationError(BacktestReportError): pass
class BacktestReportConstructionError(BacktestReportError): pass
class BacktestReportSerializationError(BacktestReportError): pass
