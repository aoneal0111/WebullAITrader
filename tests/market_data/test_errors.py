from app.market_data import *
def test_hierarchy():assert issubclass(MarketDataValidationError,MarketDataError) and issubclass(MarketDataDependencyError,MarketDataError) and issubclass(MarketDataSerializationError,MarketDataError)
