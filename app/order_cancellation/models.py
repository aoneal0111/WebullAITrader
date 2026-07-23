from dataclasses import dataclass,field
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.order_cancellation.exceptions import OrderCancellationAcknowledgementError,OrderCancellationValidationError

def _text(value,name,optional=False):
 if optional and value is None:return None
 if not isinstance(value,str) or not value.strip() or value!=value.strip():raise OrderCancellationValidationError(f"{name} must be a non-empty stripped string")
 return value
def _metadata(value):
 frozen=freeze_json_mapping("metadata",value);blocked=("password","token","cookie","authorization","secret")
 if any(any(term in str(key).lower() for term in blocked) for key in frozen):raise OrderCancellationValidationError("metadata contains a prohibited secret-bearing field")
 return frozen
class CancellationAcknowledgementState(StrEnum):CANCELED="CANCELED";REJECTED="REJECTED";NOT_FOUND="NOT_FOUND";FAILED="FAILED";NOT_SENT="NOT_SENT"
class OrderCancellationDecision(StrEnum):DISABLED="DISABLED";SESSION_INVALID="SESSION_INVALID";ORDER_NOT_FOUND="ORDER_NOT_FOUND";CANCELLATION_REJECTED="CANCELLATION_REJECTED";GATEWAY_FAILURE="GATEWAY_FAILURE";SUCCESS="SUCCESS"
@dataclass(frozen=True,slots=True)
class OrderCancellationRequest:
 request_id:str;session_id:str;account_id:str;broker_order_id:str;client_order_id:str|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  for name in ("request_id","session_id","account_id","broker_order_id"):object.__setattr__(self,name,_text(getattr(self,name),name))
  object.__setattr__(self,"client_order_id",_text(self.client_order_id,"client_order_id",True));object.__setattr__(self,"metadata",_metadata(self.metadata))
 def to_dict(self):return {"request_id":self.request_id,"session_id":self.session_id,"account_id":self.account_id,"broker_order_id":self.broker_order_id,"client_order_id":self.client_order_id,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:return cls(**dict(value))
  except OrderCancellationValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise OrderCancellationValidationError("invalid order cancellation request") from exc
@dataclass(frozen=True,slots=True)
class BrokerOrderCancellationAcknowledgement:
 broker_order_id:str;client_order_id:str|None;accepted:bool;message:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"broker_order_id",_text(self.broker_order_id,"broker_order_id"));object.__setattr__(self,"client_order_id",_text(self.client_order_id,"client_order_id",True));object.__setattr__(self,"message",_text(self.message,"message"))
  if not isinstance(self.accepted,bool):raise OrderCancellationAcknowledgementError("accepted must be boolean")
  object.__setattr__(self,"metadata",_metadata(self.metadata))
 def to_dict(self):return {"broker_order_id":self.broker_order_id,"client_order_id":self.client_order_id,"accepted":self.accepted,"message":self.message,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:return cls(**dict(value))
  except (OrderCancellationValidationError,OrderCancellationAcknowledgementError):raise
  except (TypeError,ValueError,KeyError) as exc:raise OrderCancellationAcknowledgementError("invalid cancellation acknowledgement") from exc
@dataclass(frozen=True,slots=True)
class OrderCancellationCriteriaResult:
 name:str;passed:bool;detail:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"name",_text(self.name,"criteria name"));object.__setattr__(self,"detail",_text(self.detail,"criteria detail"))
  if not isinstance(self.passed,bool):raise OrderCancellationValidationError("criteria passed must be boolean")
  object.__setattr__(self,"metadata",_metadata(self.metadata))
 def to_dict(self):return {"name":self.name,"passed":self.passed,"detail":self.detail,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):return cls(**dict(value))
@dataclass(frozen=True,slots=True)
class OrderCancellationResult:
 request_id:str;broker_order_id:str;client_order_id:str|None;decision:OrderCancellationDecision;acknowledgement_state:CancellationAcknowledgementState;gateway_message:str;criteria_results:tuple[OrderCancellationCriteriaResult,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"broker_order_id",_text(self.broker_order_id,"broker_order_id"));object.__setattr__(self,"client_order_id",_text(self.client_order_id,"client_order_id",True));object.__setattr__(self,"gateway_message",_text(self.gateway_message,"gateway_message"))
  if not isinstance(self.decision,OrderCancellationDecision) or not isinstance(self.acknowledgement_state,CancellationAcknowledgementState):raise OrderCancellationValidationError("result enums are invalid")
  expected={OrderCancellationDecision.SUCCESS:CancellationAcknowledgementState.CANCELED,OrderCancellationDecision.CANCELLATION_REJECTED:CancellationAcknowledgementState.REJECTED,OrderCancellationDecision.ORDER_NOT_FOUND:CancellationAcknowledgementState.NOT_FOUND,OrderCancellationDecision.GATEWAY_FAILURE:CancellationAcknowledgementState.FAILED,OrderCancellationDecision.DISABLED:CancellationAcknowledgementState.NOT_SENT,OrderCancellationDecision.SESSION_INVALID:CancellationAcknowledgementState.NOT_SENT}
  if self.acknowledgement_state is not expected[self.decision]:raise OrderCancellationValidationError("decision and acknowledgement state are inconsistent")
  if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,OrderCancellationCriteriaResult) for x in self.criteria_results):raise OrderCancellationValidationError("criteria_results must be immutable criteria tuple")
  object.__setattr__(self,"metadata",_metadata(self.metadata))
 @property
 def success(self):return self.decision is OrderCancellationDecision.SUCCESS
 def to_dict(self):return {"request_id":self.request_id,"broker_order_id":self.broker_order_id,"client_order_id":self.client_order_id,"decision":self.decision.value,"acknowledgement_state":self.acknowledgement_state.value,"gateway_message":self.gateway_message,"criteria_results":[x.to_dict() for x in self.criteria_results],"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["decision"]=OrderCancellationDecision(data["decision"]);data["acknowledgement_state"]=CancellationAcknowledgementState(data["acknowledgement_state"]);data["criteria_results"]=tuple(OrderCancellationCriteriaResult.from_dict(x) for x in data["criteria_results"]);return cls(**data)
  except OrderCancellationValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise OrderCancellationValidationError("invalid order cancellation result") from exc
