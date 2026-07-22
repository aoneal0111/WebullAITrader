from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal,InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.order_status.exceptions import OrderStatusSnapshotError,OrderStatusValidationError
def _text(value,name,optional=False):
 if optional and value is None:return None
 if optional and value=="":return value
 if not isinstance(value,str) or not value.strip() or value!=value.strip():raise OrderStatusValidationError(f"{name} must be a non-empty stripped string")
 return value
def _quantity(value,name,positive=False):
 if isinstance(value,bool) or not isinstance(value,(Decimal,str,int)):raise OrderStatusSnapshotError(f"{name} must be Decimal-compatible")
 try:result=Decimal(value)
 except (InvalidOperation,ValueError) as exc:raise OrderStatusSnapshotError(f"{name} must be finite") from exc
 if not result.is_finite() or result<0 or (positive and result==0):raise OrderStatusSnapshotError(f"{name} must be {'positive' if positive else 'non-negative'} and finite")
 return result
def _safe_metadata(value):
 frozen=freeze_json_mapping("metadata",value);blocked=("password","token","cookie","authorization","secret")
 if any(any(term in str(key).lower() for term in blocked) for key in frozen):raise OrderStatusValidationError("metadata contains a prohibited secret-bearing field")
 return frozen
class NormalizedOrderStatus(StrEnum):
 PENDING_SUBMISSION="PENDING_SUBMISSION";SUBMITTED="SUBMITTED";ACCEPTED="ACCEPTED";PARTIALLY_FILLED="PARTIALLY_FILLED";FILLED="FILLED";CANCELED="CANCELED";REJECTED="REJECTED";EXPIRED="EXPIRED";REPLACED="REPLACED";UNKNOWN="UNKNOWN"
class OrderStatusDecision(StrEnum):DISABLED="DISABLED";SESSION_INVALID="SESSION_INVALID";ORDER_NOT_FOUND="ORDER_NOT_FOUND";GATEWAY_FAILURE="GATEWAY_FAILURE";SUCCESS="SUCCESS"
@dataclass(frozen=True,slots=True)
class OrderStatusRequest:
 request_id:str;session_id:str;account_id:str;broker_order_id:str;client_order_id:str|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  for name in ("request_id","session_id","account_id","broker_order_id"):object.__setattr__(self,name,_text(getattr(self,name),name))
  object.__setattr__(self,"client_order_id",_text(self.client_order_id,"client_order_id",True));object.__setattr__(self,"metadata",_safe_metadata(self.metadata))
 def to_dict(self):return {"request_id":self.request_id,"session_id":self.session_id,"account_id":self.account_id,"broker_order_id":self.broker_order_id,"client_order_id":self.client_order_id,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:return cls(**dict(value))
  except OrderStatusValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise OrderStatusValidationError("invalid order status request") from exc
@dataclass(frozen=True,slots=True)
class BrokerOrderStatusSnapshot:
 broker_order_id:str;client_order_id:str|None;status:NormalizedOrderStatus;requested_quantity:Decimal;filled_quantity:Decimal;remaining_quantity:Decimal;average_fill_price:Decimal|None=None;rejection_reason:str|None=None;observed_at:datetime|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"broker_order_id",_text(self.broker_order_id,"broker_order_id"));object.__setattr__(self,"client_order_id",_text(self.client_order_id,"client_order_id",True))
  if not isinstance(self.status,NormalizedOrderStatus):raise OrderStatusSnapshotError("status must be NormalizedOrderStatus")
  requested=_quantity(self.requested_quantity,"requested_quantity",True);filled=_quantity(self.filled_quantity,"filled_quantity");remaining=_quantity(self.remaining_quantity,"remaining_quantity")
  if filled>requested:raise OrderStatusSnapshotError("filled quantity cannot exceed requested quantity")
  if remaining!=requested-filled:raise OrderStatusSnapshotError("remaining quantity is inconsistent")
  object.__setattr__(self,"requested_quantity",requested);object.__setattr__(self,"filled_quantity",filled);object.__setattr__(self,"remaining_quantity",remaining)
  if self.average_fill_price is not None:object.__setattr__(self,"average_fill_price",_quantity(self.average_fill_price,"average_fill_price",True))
  if filled>0 and self.average_fill_price is None:raise OrderStatusSnapshotError("filled order requires average_fill_price")
  if filled==0 and self.average_fill_price is not None:raise OrderStatusSnapshotError("unfilled order cannot have average_fill_price")
  if self.status is NormalizedOrderStatus.FILLED and filled!=requested:raise OrderStatusSnapshotError("FILLED requires full quantity")
  if self.status is NormalizedOrderStatus.PARTIALLY_FILLED and not (0<filled<requested):raise OrderStatusSnapshotError("PARTIALLY_FILLED requires a partial quantity")
  object.__setattr__(self,"rejection_reason",_text(self.rejection_reason,"rejection_reason",True))
  if self.rejection_reason is not None and self.status is not NormalizedOrderStatus.REJECTED:raise OrderStatusSnapshotError("rejection reason is valid only for REJECTED status")
  if self.observed_at is not None and (not isinstance(self.observed_at,datetime) or self.observed_at.tzinfo is None):raise OrderStatusSnapshotError("observed_at must be timezone-aware")
  object.__setattr__(self,"metadata",_safe_metadata(self.metadata))
 def to_dict(self):return {"broker_order_id":self.broker_order_id,"client_order_id":self.client_order_id,"status":self.status.value,"requested_quantity":str(self.requested_quantity),"filled_quantity":str(self.filled_quantity),"remaining_quantity":str(self.remaining_quantity),"average_fill_price":str(self.average_fill_price) if self.average_fill_price is not None else None,"rejection_reason":self.rejection_reason,"observed_at":self.observed_at.isoformat() if self.observed_at else None,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["status"]=NormalizedOrderStatus(data["status"]);data["observed_at"]=datetime.fromisoformat(data["observed_at"]) if data.get("observed_at") else None;return cls(**data)
  except (OrderStatusValidationError,OrderStatusSnapshotError):raise
  except (TypeError,ValueError,KeyError) as exc:raise OrderStatusSnapshotError("invalid order status snapshot") from exc
@dataclass(frozen=True,slots=True)
class OrderStatusCriteriaResult:
 name:str;passed:bool;detail:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"name",_text(self.name,"criteria name"));object.__setattr__(self,"detail",_text(self.detail,"criteria detail"))
  if not isinstance(self.passed,bool):raise OrderStatusValidationError("criteria passed must be boolean")
  object.__setattr__(self,"metadata",_safe_metadata(self.metadata))
 def to_dict(self):return {"name":self.name,"passed":self.passed,"detail":self.detail,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):return cls(**dict(value))
@dataclass(frozen=True,slots=True)
class OrderStatusResult:
 request_id:str;broker_order_id:str;client_order_id:str|None;decision:OrderStatusDecision;snapshot:BrokerOrderStatusSnapshot|None;criteria_results:tuple[OrderStatusCriteriaResult,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"broker_order_id",_text(self.broker_order_id,"broker_order_id"));object.__setattr__(self,"client_order_id",_text(self.client_order_id,"client_order_id",True))
  if not isinstance(self.decision,OrderStatusDecision):raise OrderStatusValidationError("decision must be OrderStatusDecision")
  if self.decision is OrderStatusDecision.SUCCESS and not isinstance(self.snapshot,BrokerOrderStatusSnapshot):raise OrderStatusValidationError("successful result requires snapshot")
  if self.decision is not OrderStatusDecision.SUCCESS and self.snapshot is not None:raise OrderStatusValidationError("failure result cannot expose snapshot")
  if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,OrderStatusCriteriaResult) for x in self.criteria_results):raise OrderStatusValidationError("criteria_results must be immutable criteria tuple")
  object.__setattr__(self,"metadata",_safe_metadata(self.metadata))
 @property
 def success(self):return self.decision is OrderStatusDecision.SUCCESS
 def to_dict(self):return {"request_id":self.request_id,"broker_order_id":self.broker_order_id,"client_order_id":self.client_order_id,"decision":self.decision.value,"snapshot":self.snapshot.to_dict() if self.snapshot else None,"criteria_results":[x.to_dict() for x in self.criteria_results],"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["decision"]=OrderStatusDecision(data["decision"]);data["snapshot"]=BrokerOrderStatusSnapshot.from_dict(data["snapshot"]) if data["snapshot"] else None;data["criteria_results"]=tuple(OrderStatusCriteriaResult.from_dict(x) for x in data["criteria_results"]);return cls(**data)
  except OrderStatusValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise OrderStatusValidationError("invalid order status result") from exc
