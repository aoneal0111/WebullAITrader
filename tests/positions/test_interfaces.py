from inspect import signature
from app.positions import *
def test_exact_runtime_and_gateway_interfaces():
 assert {n for n in dir(DeterministicPositionsRuntime) if not n.startswith("_")}=={"get_positions"}
 assert list(signature(PositionsRuntime.get_positions).parameters)==["self","request"]
 assert list(signature(BrokerPositionGateway.get_positions).parameters)==["self","request"]
