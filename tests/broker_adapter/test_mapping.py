from app.broker_adapter import *
from tests.broker_adapter.helpers import request
def test_mapping_deterministic_limit_buy():
 r=request();a=BrokerOrderMapper().map(r);b=BrokerOrderMapper().map(r);assert a==b and a.side is BrokerOrderSide.BUY and a.limit_price==r.invocation.entry_price
def test_market_uses_zero_price():
 r=request(order_type=BrokerOrderType.MARKET,policy=__import__("tests.broker_adapter.helpers",fromlist=["policy"]).policy(allowed_order_types=(BrokerOrderType.MARKET,)));assert BrokerOrderMapper().map(r).limit_price==0
