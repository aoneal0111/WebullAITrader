from inspect import signature
from app.order_cancellation import *
def test_exact_interfaces():
 assert {n for n in dir(DeterministicOrderCancellationRuntime) if not n.startswith("_")}=={"cancel_order"}
 assert list(signature(OrderCancellationRuntime.cancel_order).parameters)==["self","request"]
 assert list(signature(BrokerOrderCancellationGateway.cancel_order).parameters)==["self","request"]
