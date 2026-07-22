from datetime import UTC,datetime,timedelta
from decimal import Decimal
from app.webull_gateway import *
from app.webull_transport import WebullGatewayResponse,WebullGatewayStatus
STAMP=datetime(2026,7,21,20,6,tzinfo=UTC)
class FakeGateway:
 def __init__(self):self.received=[]
 def authenticate(self,r):self.received.append(r);return LoginResponse(True,STAMP,STAMP+timedelta(minutes=5),r.environment)
 def logout(self,r):self.received.append(r);return LogoutResponse(True,STAMP,r.environment)
 def get_account(self,r):self.received.append(r);return AccountResponse(1000,500,1500,{"AAPL":1},10,STAMP,r.environment)
 def submit_order(self,r):self.received.append(r);return SubmitOrderResponse(True,"opaque",r.command.quantity,r.command.limit_price,STAMP,False)
 def cancel_order(self,r):self.received.append(r);return CancelOrderResponse(True,r.broker_reference,STAMP,False)
 def get_order_status(self,r):self.received.append(r);return OrderStatusResponse(r.broker_reference,NormalizedOrderStatus.SUBMITTED,1,0,0,STAMP)
 def place_order(self,c):
  result=self.submit_order(SubmitOrderRequest(c,STAMP,c.environment));return WebullGatewayResponse(c.transport_request_id,c.client_order_id,result.broker_reference,WebullGatewayStatus.ACCEPTED,result.quantity,result.price,result.timestamp,"","",result.retryable)
