from inspect import signature
from app.order_status import *
def test_exact_interfaces_and_no_mutating_operations():
 assert {n for n in dir(DeterministicOrderStatusRuntime) if not n.startswith("_")}=={"get_order_status"}
 assert list(signature(OrderStatusRuntime.get_order_status).parameters)==["self","request"]
 assert list(signature(BrokerOrderStatusGateway.get_order_status).parameters)==["self","request"]
