from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal,InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.order_placement.models import OrderSide,OrderType
from app.order_status.models import NormalizedOrderStatus
from app.open_orders.exceptions import OpenOrdersSnapshotError,OpenOrdersValidationError
def _text(value,name,optional=False):
 if optional and value is None:return None
 if not isinstance(value,str) or not value.strip() or value!=value.strip():raise OpenOrdersValidationError(f"{name} must be a non-empty stripped string")
 return value
def _decimal(value,name,optional=False):
 if optional and value is None:return None
 if isinstance(value,bool) or not isinstance(value,(Decimal,str,int)):raise OpenOrdersSnapshotError(f"{name} must be Decimal-compatible")
 try:result=Decimal(value)
 except (InvalidOperation,ValueError) as exc:raise OpenOrdersSnapshotError(f"{name} must be finite") from exc
 if not result.is_finite() or result<=0:raise OpenOrdersSnapshotError(f"{name} must be positive and finite")
 return result
class OpenOrdersDecision(StrEnum):DISABLED="DISABLED";SESSION_INVALID="SESSION_INVALID";GATEWAY_FAILURE="GATEWAY_FAILURE";SUCCESS="SUCCESS"
OPEN_STATUSES=frozenset((NormalizedOrderStatus.PENDING_SUBMISSION,NormalizedOrderStatus.SUBMITTED,NormalizedOrderStatus.ACCEPTED,NormalizedOrderStatus.PARTIALLY_FILLED,NormalizedOrderStatus.REPLACED,NormalizedOrderStatus.UNKNOWN))
@dataclass(frozen=True,slots=True)
class OpenOrdersRequest:
 request_id:str;account_id:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"account_id",_text(self.account_id,"account_id"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"request_id":self.request_id,"account_id":self.account_id,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:return cls(**dict(value))
  except OpenOrdersValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise OpenOrdersValidationError("invalid open orders request") from exc
@dataclass(frozen=True,slots=True)
class OpenOrderSnapshot:
 broker_order_id:str;client_order_id:str|None;account_id:str;symbol:str;side:OrderSide;order_type:OrderType;status:NormalizedOrderStatus;requested_quantity:Decimal;remaining_quantity:Decimal;limit_price:Decimal|None=None;stop_price:Decimal|None=None;average_fill_price:Decimal|None=None;submitted_at:datetime|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  for name in ("broker_order_id","account_id"):object.__setattr__(self,name,_text(getattr(self,name),name))
  object.__setattr__(self,"client_order_id",_text(self.client_order_id,"client_order_id",True));object.__setattr__(self,"symbol",_text(self.symbol,"symbol").upper())
  if not isinstance(self.side,OrderSide) or not isinstance(self.order_type,OrderType):raise OpenOrdersSnapshotError("order side or type is invalid")
  if not isinstance(self.status,NormalizedOrderStatus) or self.status not in OPEN_STATUSES:raise OpenOrdersSnapshotError("terminal or invalid status cannot be an open order")
  requested=_decimal(self.requested_quantity,"requested_quantity");remaining=_decimal(self.remaining_quantity,"remaining_quantity")
  if remaining>requested:raise OpenOrdersSnapshotError("remaining quantity cannot exceed requested quantity")
  object.__setattr__(self,"requested_quantity",requested);object.__setattr__(self,"remaining_quantity",remaining)
  for name in ("limit_price","stop_price","average_fill_price"):object.__setattr__(self,name,_decimal(getattr(self,name),name,True))
  if self.order_type in (OrderType.LIMIT,OrderType.STOP_LIMIT) and self.limit_price is None:raise OpenOrdersSnapshotError("limit order requires limit_price")
  if self.order_type in (OrderType.STOP,OrderType.STOP_LIMIT) and self.stop_price is None:raise OpenOrdersSnapshotError("stop order requires stop_price")
  if self.status is NormalizedOrderStatus.PARTIALLY_FILLED and (remaining>=requested or self.average_fill_price is None):raise OpenOrdersSnapshotError("partial fill requires reduced remaining quantity and average price")
  if self.submitted_at is not None and (not isinstance(self.submitted_at,datetime) or self.submitted_at.tzinfo is None):raise OpenOrdersSnapshotError("submitted_at must be timezone-aware")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"broker_order_id":self.broker_order_id,"client_order_id":self.client_order_id,"account_id":self.account_id,"symbol":self.symbol,"side":self.side.value,"order_type":self.order_type.value,"status":self.status.value,"requested_quantity":str(self.requested_quantity),"remaining_quantity":str(self.remaining_quantity),"limit_price":str(self.limit_price) if self.limit_price is not None else None,"stop_price":str(self.stop_price) if self.stop_price is not None else None,"average_fill_price":str(self.average_fill_price) if self.average_fill_price is not None else None,"submitted_at":self.submitted_at.isoformat() if self.submitted_at else None,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["side"]=OrderSide(data["side"]);data["order_type"]=OrderType(data["order_type"]);data["status"]=NormalizedOrderStatus(data["status"]);data["submitted_at"]=datetime.fromisoformat(data["submitted_at"]) if data.get("submitted_at") else None;return cls(**data)
  except (OpenOrdersValidationError,OpenOrdersSnapshotError):raise
  except (TypeError,ValueError,KeyError) as exc:raise OpenOrdersSnapshotError("invalid open order snapshot") from exc
@dataclass(frozen=True,slots=True)
class OpenOrdersCriteriaResult:
 name:str;passed:bool;detail:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"name",_text(self.name,"criteria name"));object.__setattr__(self,"detail",_text(self.detail,"criteria detail"))
  if not isinstance(self.passed,bool):raise OpenOrdersValidationError("criteria passed must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"name":self.name,"passed":self.passed,"detail":self.detail,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):return cls(**dict(value))
@dataclass(frozen=True,slots=True)
class OpenOrdersResult:
 request_id:str;account_id:str;decision:OpenOrdersDecision;orders:tuple[OpenOrderSnapshot,...];criteria_results:tuple[OpenOrdersCriteriaResult,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"account_id",_text(self.account_id,"account_id"))
  if not isinstance(self.decision,OpenOrdersDecision):raise OpenOrdersValidationError("decision must be OpenOrdersDecision")
  if not isinstance(self.orders,tuple) or any(not isinstance(x,OpenOrderSnapshot) for x in self.orders):raise OpenOrdersValidationError("orders must be an immutable snapshot tuple")
  if self.decision is not OpenOrdersDecision.SUCCESS and self.orders:raise OpenOrdersValidationError("failure result cannot expose orders")
  if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,OpenOrdersCriteriaResult) for x in self.criteria_results):raise OpenOrdersValidationError("criteria_results must be immutable criteria tuple")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 @property
 def success(self):return self.decision is OpenOrdersDecision.SUCCESS
 def to_dict(self):return {"request_id":self.request_id,"account_id":self.account_id,"decision":self.decision.value,"orders":[x.to_dict() for x in self.orders],"criteria_results":[x.to_dict() for x in self.criteria_results],"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["decision"]=OpenOrdersDecision(data["decision"]);data["orders"]=tuple(OpenOrderSnapshot.from_dict(x) for x in data["orders"]);data["criteria_results"]=tuple(OpenOrdersCriteriaResult.from_dict(x) for x in data["criteria_results"]);return cls(**data)
  except OpenOrdersValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise OpenOrdersValidationError("invalid open orders result") from exc
