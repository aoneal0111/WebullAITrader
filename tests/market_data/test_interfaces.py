from inspect import signature
from app.market_data import *
def test_exact_interfaces():
 assert {n for n in dir(DeterministicMarketDataRuntime) if not n.startswith("_")}=={"get_market_data"}
 assert list(signature(MarketDataRuntime.get_market_data).parameters)==["self","request"]
 assert list(signature(BrokerMarketDataGateway.get_market_data).parameters)==["self","request"]
