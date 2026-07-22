from app.market_data import MarketDataPolicy,MarketDataRequest
def request():return MarketDataRequest("request-1","session-1",("aapl","msft"),{"source":"synthetic"})
def enabled_policy():return MarketDataPolicy(enabled=True)
