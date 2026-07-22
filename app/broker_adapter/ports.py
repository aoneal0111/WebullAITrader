from typing import Protocol
from app.broker_adapter.models import BrokerOrderRequest,BrokerTransportResponse
class BrokerTransportPort(Protocol):
 def submit_order(self,request:BrokerOrderRequest)->BrokerTransportResponse:...
