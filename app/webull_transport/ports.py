from typing import Protocol
from app.webull_transport.models import WebullGatewayResponse,WebullOrderCommand
class WebullGatewayPort(Protocol):
 def place_order(self,command:WebullOrderCommand)->WebullGatewayResponse:...
