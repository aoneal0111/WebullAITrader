from dataclasses import dataclass,field
from decimal import Decimal,InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.execution_planner.exceptions import ExecutionPlannerValidationError
from app.order_placement import OrderSide,OrderType,TimeInForce
from app.risk import RiskContext,RiskResult
def _text(value,name):
 if not isinstance(value,str) or not value.strip() or value!=value.strip():raise ExecutionPlannerValidationError(f"{name} must be a non-empty stripped string")
 return value
def _decimal(value,name,optional=False):
 if optional and value is None:return None
 if isinstance(value,bool) or not isinstance(value,(Decimal,str,int)):raise ExecutionPlannerValidationError(f"{name} must be Decimal-compatible")
 try:result=Decimal(value)
 except (InvalidOperation,ValueError) as exc:raise ExecutionPlannerValidationError(f"{name} must be finite") from exc
 if not result.is_finite() or result<=0:raise ExecutionPlannerValidationError(f"{name} must be positive and finite")
 return result
class ExecutionPlanDecision(StrEnum):PLANNED="PLANNED";DISABLED="DISABLED";REJECTED="REJECTED";INVALID_RISK_RESULT="INVALID_RISK_RESULT"
@dataclass(frozen=True,slots=True)
class ExecutionPlanRequest:
 request_id:str;risk_context:RiskContext;risk_result:RiskResult;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"request_id",_text(self.request_id,"request_id"))
  if not isinstance(self.risk_context,RiskContext) or not isinstance(self.risk_result,RiskResult):raise ExecutionPlannerValidationError("risk_context and risk_result are required")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"request_id":self.request_id,"risk_context":self.risk_context.to_dict(),"risk_result":self.risk_result.to_dict(),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:return cls(value["request_id"],RiskContext.from_dict(value["risk_context"]),RiskResult.from_dict(value["risk_result"]),value.get("metadata",{}))
  except ExecutionPlannerValidationError:raise
  except (KeyError,TypeError,ValueError) as exc:raise ExecutionPlannerValidationError("invalid execution plan request") from exc
@dataclass(frozen=True,slots=True)
class ExecutionInstruction:
 account_id:str;symbol:str;side:OrderSide;quantity:Decimal;order_type:OrderType;time_in_force:TimeInForce;limit_price:Decimal|None=None;stop_price:Decimal|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"account_id",_text(self.account_id,"account_id"));object.__setattr__(self,"symbol",_text(self.symbol,"symbol").upper())
  if not isinstance(self.side,OrderSide) or not isinstance(self.order_type,OrderType) or not isinstance(self.time_in_force,TimeInForce):raise ExecutionPlannerValidationError("instruction enums are invalid")
  object.__setattr__(self,"quantity",_decimal(self.quantity,"quantity"));object.__setattr__(self,"limit_price",_decimal(self.limit_price,"limit_price",True));object.__setattr__(self,"stop_price",_decimal(self.stop_price,"stop_price",True))
  if self.order_type in (OrderType.LIMIT,OrderType.STOP_LIMIT) and self.limit_price is None:raise ExecutionPlannerValidationError("limit order requires limit_price")
  if self.order_type in (OrderType.STOP,OrderType.STOP_LIMIT) and self.stop_price is None:raise ExecutionPlannerValidationError("stop order requires stop_price")
  if self.order_type is OrderType.MARKET and (self.limit_price is not None or self.stop_price is not None):raise ExecutionPlannerValidationError("market order cannot contain prices")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"account_id":self.account_id,"symbol":self.symbol,"side":self.side.value,"quantity":str(self.quantity),"order_type":self.order_type.value,"time_in_force":self.time_in_force.value,"limit_price":str(self.limit_price) if self.limit_price is not None else None,"stop_price":str(self.stop_price) if self.stop_price is not None else None,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["side"]=OrderSide(data["side"]);data["order_type"]=OrderType(data["order_type"]);data["time_in_force"]=TimeInForce(data["time_in_force"]);return cls(**data)
  except ExecutionPlannerValidationError:raise
  except (KeyError,TypeError,ValueError) as exc:raise ExecutionPlannerValidationError("invalid execution instruction") from exc
@dataclass(frozen=True,slots=True)
class ExecutionPlan:
 request_id:str;instructions:tuple[ExecutionInstruction,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"request_id",_text(self.request_id,"request_id"))
  if not isinstance(self.instructions,tuple) or len(self.instructions)!=1 or any(not isinstance(x,ExecutionInstruction) for x in self.instructions):raise ExecutionPlannerValidationError("plan must contain exactly one instruction")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"request_id":self.request_id,"instructions":[x.to_dict() for x in self.instructions],"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:return cls(value["request_id"],tuple(ExecutionInstruction.from_dict(x) for x in value["instructions"]),value.get("metadata",{}))
  except ExecutionPlannerValidationError:raise
  except (KeyError,TypeError,ValueError) as exc:raise ExecutionPlannerValidationError("invalid execution plan") from exc
@dataclass(frozen=True,slots=True)
class ExecutionPlanCriteriaResult:
 name:str;passed:bool;detail:str
 def __post_init__(self):
  object.__setattr__(self,"name",_text(self.name,"criteria name"));object.__setattr__(self,"detail",_text(self.detail,"criteria detail"))
  if not isinstance(self.passed,bool):raise ExecutionPlannerValidationError("criteria passed must be boolean")
 def to_dict(self):return {"name":self.name,"passed":self.passed,"detail":self.detail}
 @classmethod
 def from_dict(cls,value):return cls(**dict(value))
@dataclass(frozen=True,slots=True)
class ExecutionPlanResult:
 request_id:str;decision:ExecutionPlanDecision;plan:ExecutionPlan|None;criteria_results:tuple[ExecutionPlanCriteriaResult,...];policy_version:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"policy_version",_text(self.policy_version,"policy_version"))
  if not isinstance(self.decision,ExecutionPlanDecision):raise ExecutionPlannerValidationError("decision must be ExecutionPlanDecision")
  if self.decision is ExecutionPlanDecision.PLANNED and not isinstance(self.plan,ExecutionPlan):raise ExecutionPlannerValidationError("planned result requires plan")
  if self.decision is not ExecutionPlanDecision.PLANNED and self.plan is not None:raise ExecutionPlannerValidationError("non-planned result cannot expose plan")
  if not isinstance(self.criteria_results,tuple) or not self.criteria_results or any(not isinstance(x,ExecutionPlanCriteriaResult) for x in self.criteria_results):raise ExecutionPlannerValidationError("criteria_results must be non-empty immutable tuple")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"request_id":self.request_id,"decision":self.decision.value,"plan":self.plan.to_dict() if self.plan else None,"criteria_results":[x.to_dict() for x in self.criteria_results],"policy_version":self.policy_version,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:return cls(value["request_id"],ExecutionPlanDecision(value["decision"]),ExecutionPlan.from_dict(value["plan"]) if value["plan"] else None,tuple(ExecutionPlanCriteriaResult.from_dict(x) for x in value["criteria_results"]),value["policy_version"],value.get("metadata",{}))
  except ExecutionPlannerValidationError:raise
  except (KeyError,TypeError,ValueError) as exc:raise ExecutionPlannerValidationError("invalid execution plan result") from exc
