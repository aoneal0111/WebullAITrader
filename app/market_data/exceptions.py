class MarketDataError(Exception):
    """Base error for deterministic market-data retrieval."""
class MarketDataValidationError(MarketDataError): pass
class MarketDataDependencyError(MarketDataError): pass
class MarketDataSerializationError(MarketDataError): pass
