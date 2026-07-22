from inspect import signature
from app.order_placement import *
def test_exact_interfaces():
 assert {n for n in dir(DeterministicOrderPlacementRuntime) if not n.startswith("_")}=={"place_order"}
 assert list(signature(OrderPlacementRuntime.place_order).parameters)==["self","request"]
 assert list(signature(BrokerOrderPlacementGateway.place_order).parameters)==["self","request"]
