from app.broker_adapter import BrokerOrderRequest,BrokerOrderType,BrokerTimeInForce,BrokerTransportResponse,BrokerTransportStatus
from app.webull_transport.mapping import WebullOrderMapper,request_id
from app.webull_transport.models import *
from app.webull_transport.models_base import *
class WebullTransport:
 name="webull_transport_v1"
 def __init__(self,gateway,policy:WebullTransportPolicy,state:WebullTransportState,timestamp):
  if not hasattr(gateway,"place_order"):raise ValueError("gateway must implement place_order")
  self.gateway=gateway;self.context=(policy,state,timestamp);self.mapper=WebullOrderMapper()
 def submit_order(self,b):
  if not isinstance(b,BrokerOrderRequest):raise ValueError("request must be BrokerOrderRequest")
  p,s,t=self.context;r=WebullTransportRequest(b,t,p,s);cmd=self.mapper.map(r);notional=b.limit_price*b.quantity
  checks=(p.transport_enabled,b.environment==p.required_environment,b.symbol in p.allowed_symbols,b.quantity>0,b.order_type in (BrokerOrderType.LIMIT,BrokerOrderType.MARKET),not p.require_limit_orders or b.order_type is BrokerOrderType.LIMIT,b.time_in_force in (BrokerTimeInForce.DAY,BrokerTimeInForce.GTC),not p.require_day_time_in_force or b.time_in_force is BrokerTimeInForce.DAY,(b.order_type is BrokerOrderType.MARKET and b.limit_price==0) or (b.order_type is BrokerOrderType.LIMIT and b.limit_price>0),b.quantity<=p.maximum_quantity,notional<=p.maximum_notional,not p.reject_duplicate_transport_request_ids or cmd.transport_request_id not in s.submitted_transport_request_ids)
  codes=(WebullRejectionCode.TRANSPORT_DISABLED,WebullRejectionCode.ENVIRONMENT_MISMATCH,WebullRejectionCode.SYMBOL_NOT_ALLOWED,WebullRejectionCode.INVALID_QUANTITY,WebullRejectionCode.ORDER_TYPE_NOT_ALLOWED,WebullRejectionCode.ORDER_TYPE_NOT_ALLOWED,WebullRejectionCode.TIME_IN_FORCE_NOT_ALLOWED,WebullRejectionCode.TIME_IN_FORCE_NOT_ALLOWED,WebullRejectionCode.INVALID_LIMIT_PRICE,WebullRejectionCode.QUANTITY_EXCEEDS_LIMIT,WebullRejectionCode.NOTIONAL_EXCEEDS_LIMIT,WebullRejectionCode.DUPLICATE_TRANSPORT_REQUEST)
  failed=next((i for i,x in enumerate(checks) if not x),None)
  if failed is not None:return self._failure(b,cmd.transport_request_id,t,codes[failed])
  try:g=self.gateway.place_order(cmd)
  except Exception:return self._failure(b,cmd.transport_request_id,t,WebullRejectionCode.GATEWAY_FAILURE,True)
  if not isinstance(g,WebullGatewayResponse):return self._failure(b,cmd.transport_request_id,t,WebullRejectionCode.GATEWAY_FAILURE)
  if g.transport_request_id!=cmd.transport_request_id:return self._failure(b,cmd.transport_request_id,g.timestamp,WebullRejectionCode.RESPONSE_REQUEST_ID_MISMATCH)
  if g.client_order_id!=b.client_order_id:return self._failure(b,cmd.transport_request_id,g.timestamp,WebullRejectionCode.RESPONSE_CLIENT_ID_MISMATCH)
  if g.timestamp<b.submitted_at:return self._failure(b,cmd.transport_request_id,g.timestamp,WebullRejectionCode.RESPONSE_TIMESTAMP_INVALID)
  if g.accepted_quantity>b.quantity:return self._failure(b,cmd.transport_request_id,g.timestamp,WebullRejectionCode.RESPONSE_QUANTITY_INVALID)
  status={WebullGatewayStatus.ACCEPTED:BrokerTransportStatus.ACCEPTED,WebullGatewayStatus.REJECTED:BrokerTransportStatus.REJECTED,WebullGatewayStatus.FAILED:BrokerTransportStatus.FAILED,WebullGatewayStatus.UNKNOWN:BrokerTransportStatus.UNKNOWN}[g.status]
  return BrokerTransportResponse(b.client_order_id,g.transport_request_id,g.broker_order_reference,status,g.accepted_quantity,g.accepted_price,g.timestamp,g.rejection_code,g.rejection_message,g.retryable,{"deterministic":True,"transport_version":self.name})
 def _failure(self,b,tid,t,code,retry=False):return BrokerTransportResponse(b.client_order_id,tid,"",BrokerTransportStatus.FAILED,0,b.limit_price*0,t,code.value,code.value,retry,{"deterministic":True,"transport_version":self.name})
