from typing import get_type_hints
from app.broker import BrokerOrderExecutor,BrokerOrderRequest,BrokerOrderResult

def test_executor_protocol_matches_place_order_boundary():
    hints=get_type_hints(BrokerOrderExecutor.place_order)
    assert hints["request"] is BrokerOrderRequest and hints["return"] is BrokerOrderResult

def test_protocol_compatible_executor_needs_only_place_order():
    class Compatible:
        def place_order(self,request):return request
    assert callable(getattr(Compatible(),"place_order",None))
