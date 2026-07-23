class LiveTradingError(Exception): pass
class LiveTradingValidationError(LiveTradingError): pass
class LiveTradingDependencyError(LiveTradingError): pass
class LiveTradingSerializationError(LiveTradingError): pass
