from inspect import signature
from app.open_orders import *
def test_exact_public_interfaces():
 assert {n for n in dir(DeterministicOpenOrdersRuntime) if not n.startswith("_")}=={"get_open_orders"}
 assert list(signature(OpenOrdersRuntime.get_open_orders).parameters)==["self","request"]
 assert list(signature(BrokerOpenOrdersGateway.get_open_orders).parameters)==["self","request"]
