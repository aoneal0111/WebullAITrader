from dataclasses import dataclass,field
from decimal import Decimal,InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.order_placement.exceptions import OrderPlacementValidationError
def _text(value,name,optional=False):
 if optional and value=="":return value
 if not isinstance(value,str) or not value.strip() or value!=value.strip():raise OrderPlacementValidationError(f"{name} must be a non-empty stripped string")
 return value
def _decimal(value,name,optional=False):
 if value is None and optional:return None
 if isinstance(value,bool) or not isinstance(value,(Decimal,str,int)):raise OrderPlacementValidationError(f"{name} must be Decimal-compatible")
 try:result=Decimal(value)
 except (InvalidOperation,ValueError) as exc:raise OrderPlacementValidationError(f"{name} must be a finite Decimal") from exc
 if not result.is_finite() or result<=0:raise OrderPlacementValidationError(f"{name} must be positive and finite")
 return result
class OrderSide(StrEnum):BUY="BUY";SELL="SELL"
class OrderType(StrEnum):MARKET="MARKET";LIMIT="LIMIT";STOP="STOP";STOP_LIMIT="STOP_LIMIT"
class TimeInForce(StrEnum):DAY="DAY";GTC="GTC"
class AcknowledgementState(StrEnum):ACCEPTED="ACCEPTED";REJECTED="REJECTED";FAILED="FAILED";NOT_SENT="NOT_SENT"
class NormalizedOrderStatus(StrEnum):SUBMITTED="SUBMITTED";REJECTED="REJECTED";FAILED="FAILED";NOT_SUBMITTED="NOT_SUBMITTED"
class OrderPlacementDecision(StrEnum):DISABLED="DISABLED";SESSION_INVALID="SESSION_INVALID";ORDER_REJECTED="ORDER_REJECTED";GATEWAY_FAILURE="GATEWAY_FAILURE";SUCCESS="SUCCESS"
@dataclass(frozen=True,slots=True)
class OrderRequestModel:
 request_id:str;account_id:str;symbol:str;side:OrderSide;order_type:OrderType;quantity:Decimal;limit_price:Decimal|None;stop_price:Decimal|None;time_in_force:TimeInForce;client_order_id:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict);strategy_lifecycle_id:str|None=None
 def __post_init__(self):
  for name in ("request_id","account_id","client_order_id"):object.__setattr__(self,name,_text(getattr(self,name),name))
  object.__setattr__(self,"symbol",_text(self.symbol,"symbol").upper())
  if not isinstance(self.side,OrderSide):raise OrderPlacementValidationError("side must be OrderSide")
  if not isinstance(self.order_type,OrderType):raise OrderPlacementValidationError("order_type must be OrderType")
  if not isinstance(self.time_in_force,TimeInForce):raise OrderPlacementValidationError("time_in_force must be TimeInForce")
  object.__setattr__(self,"quantity",_decimal(self.quantity,"quantity"));object.__setattr__(self,"limit_price",_decimal(self.limit_price,"limit_price",True));object.__setattr__(self,"stop_price",_decimal(self.stop_price,"stop_price",True))
  if self.order_type in (OrderType.LIMIT,OrderType.STOP_LIMIT) and self.limit_price is None:raise OrderPlacementValidationError("limit order requires limit_price")
  if self.order_type in (OrderType.STOP,OrderType.STOP_LIMIT) and self.stop_price is None:raise OrderPlacementValidationError("stop order requires stop_price")
  if self.order_type is OrderType.MARKET and (self.limit_price is not None or self.stop_price is not None):raise OrderPlacementValidationError("market order cannot contain prices")
  if self.order_type is OrderType.LIMIT and self.stop_price is not None:raise OrderPlacementValidationError("limit order cannot contain stop_price")
  if self.order_type is OrderType.STOP and self.limit_price is not None:raise OrderPlacementValidationError("stop order cannot contain limit_price")
  lifecycle_id = self.strategy_lifecycle_id.strip() if self.strategy_lifecycle_id and self.strategy_lifecycle_id.strip() else None
  object.__setattr__(self,"strategy_lifecycle_id",lifecycle_id)
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"request_id":self.request_id,"account_id":self.account_id,"symbol":self.symbol,"side":self.side.value,"order_type":self.order_type.value,"quantity":str(self.quantity),"limit_price":str(self.limit_price) if self.limit_price is not None else None,"stop_price":str(self.stop_price) if self.stop_price is not None else None,"time_in_force":self.time_in_force.value,"client_order_id":self.client_order_id,"strategy_lifecycle_id":self.strategy_lifecycle_id,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["side"]=OrderSide(data["side"]);data["order_type"]=OrderType(data["order_type"]);data["time_in_force"]=TimeInForce(data["time_in_force"]);return cls(**data)
  except OrderPlacementValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise OrderPlacementValidationError("invalid order request model") from exc
@dataclass(frozen=True,slots=True)
class OrderPlacementRequest:
 session_id:str;order:OrderRequestModel;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"session_id",_text(self.session_id,"session_id"))
  if not isinstance(self.order,OrderRequestModel):raise OrderPlacementValidationError("order must be OrderRequestModel")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"session_id":self.session_id,"order":self.order.to_dict(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["order"]=OrderRequestModel.from_dict(data["order"]);return cls(**data)
  except OrderPlacementValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise OrderPlacementValidationError("invalid order placement request") from exc
@dataclass(frozen=True,slots=True)
class BrokerOrderAcknowledgement:
 client_order_id:str;broker_order_id:str;accepted:bool;status:NormalizedOrderStatus;message:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"client_order_id",_text(self.client_order_id,"client_order_id"));object.__setattr__(self,"broker_order_id",_text(self.broker_order_id,"broker_order_id",True));object.__setattr__(self,"message",_text(self.message,"message"))
  if not isinstance(self.accepted,bool):raise OrderPlacementValidationError("accepted must be boolean")
  if not isinstance(self.status,NormalizedOrderStatus):raise OrderPlacementValidationError("status must be NormalizedOrderStatus")
  if self.accepted and (not self.broker_order_id or self.status is not NormalizedOrderStatus.SUBMITTED):raise OrderPlacementValidationError("accepted acknowledgement requires submitted status and broker order ID")
  if not self.accepted and self.status is NormalizedOrderStatus.SUBMITTED:raise OrderPlacementValidationError("rejected acknowledgement cannot be submitted")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"client_order_id":self.client_order_id,"broker_order_id":self.broker_order_id,"accepted":self.accepted,"status":self.status.value,"message":self.message,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["status"]=NormalizedOrderStatus(data["status"]);return cls(**data)
  except OrderPlacementValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise OrderPlacementValidationError("invalid broker order acknowledgement") from exc
@dataclass(frozen=True,slots=True)
class OrderPlacementCriteriaResult:
 name:str;passed:bool;detail:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"name",_text(self.name,"criteria name"));object.__setattr__(self,"detail",_text(self.detail,"criteria detail"))
  if not isinstance(self.passed,bool):raise OrderPlacementValidationError("criteria passed must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"name":self.name,"passed":self.passed,"detail":self.detail,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):return cls(**dict(value))
@dataclass(frozen=True,slots=True)
class OrderPlacementResult:
 request_id:str;client_order_id:str;broker_order_id:str;acknowledgement_state:AcknowledgementState;normalized_order_status:NormalizedOrderStatus;decision:OrderPlacementDecision;gateway_message:str;criteria_results:tuple[OrderPlacementCriteriaResult,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"client_order_id",_text(self.client_order_id,"client_order_id"));object.__setattr__(self,"broker_order_id",_text(self.broker_order_id,"broker_order_id",True));object.__setattr__(self,"gateway_message",_text(self.gateway_message,"gateway_message"))
  if not isinstance(self.acknowledgement_state,AcknowledgementState) or not isinstance(self.normalized_order_status,NormalizedOrderStatus) or not isinstance(self.decision,OrderPlacementDecision):raise OrderPlacementValidationError("result enums are invalid")
  if self.decision is OrderPlacementDecision.SUCCESS and (self.acknowledgement_state is not AcknowledgementState.ACCEPTED or self.normalized_order_status is not NormalizedOrderStatus.SUBMITTED or not self.broker_order_id):raise OrderPlacementValidationError("successful result requires accepted submitted acknowledgement")
  if self.decision is not OrderPlacementDecision.SUCCESS and self.broker_order_id:raise OrderPlacementValidationError("failure result cannot expose broker order ID")
  if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,OrderPlacementCriteriaResult) for x in self.criteria_results):raise OrderPlacementValidationError("criteria_results must be immutable criteria tuple")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 @property
 def success(self):return self.decision is OrderPlacementDecision.SUCCESS
 def to_dict(self):return {"request_id":self.request_id,"client_order_id":self.client_order_id,"broker_order_id":self.broker_order_id,"acknowledgement_state":self.acknowledgement_state.value,"normalized_order_status":self.normalized_order_status.value,"decision":self.decision.value,"gateway_message":self.gateway_message,"criteria_results":[x.to_dict() for x in self.criteria_results],"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["acknowledgement_state"]=AcknowledgementState(data["acknowledgement_state"]);data["normalized_order_status"]=NormalizedOrderStatus(data["normalized_order_status"]);data["decision"]=OrderPlacementDecision(data["decision"]);data["criteria_results"]=tuple(OrderPlacementCriteriaResult.from_dict(x) for x in data["criteria_results"]);return cls(**data)
  except OrderPlacementValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise OrderPlacementValidationError("invalid order placement result") from exc
