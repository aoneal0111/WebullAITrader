from dataclasses import dataclass,field
from decimal import Decimal,InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.portfolio.exceptions import PortfolioValidationError
def _text(value,name):
 if not isinstance(value,str) or not value.strip() or value!=value.strip():raise PortfolioValidationError(f"{name} must be a non-empty stripped string")
 return value
def _decimal(value,name):
 if isinstance(value,bool) or not isinstance(value,(Decimal,str,int)):raise PortfolioValidationError(f"{name} must be Decimal-compatible")
 try:result=Decimal(value)
 except (InvalidOperation,ValueError) as exc:raise PortfolioValidationError(f"{name} must be finite") from exc
 if not result.is_finite():raise PortfolioValidationError(f"{name} must be finite")
 return result
class PortfolioDecision(StrEnum):SUCCESS="SUCCESS";DISABLED="DISABLED";DEPENDENCY_FAILURE="DEPENDENCY_FAILURE";INVALID_ACCOUNT="INVALID_ACCOUNT"
@dataclass(frozen=True,slots=True)
class PortfolioRequest:
 request_id:str;account_id:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"account_id",_text(self.account_id,"account_id"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"request_id":self.request_id,"account_id":self.account_id,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:return cls(**dict(value))
  except PortfolioValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise PortfolioValidationError("invalid portfolio request") from exc
@dataclass(frozen=True,slots=True)
class PortfolioPosition:
 symbol:str;quantity:Decimal;market_value:Decimal;cost_basis:Decimal;unrealized_pl:Decimal;weight:Decimal;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"symbol",_text(self.symbol,"symbol").upper())
  for name in ("quantity","market_value","cost_basis","unrealized_pl","weight"):object.__setattr__(self,name,_decimal(getattr(self,name),name))
  if self.quantity==0:raise PortfolioValidationError("quantity must be non-zero")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"symbol":self.symbol,"quantity":str(self.quantity),"market_value":str(self.market_value),"cost_basis":str(self.cost_basis),"unrealized_pl":str(self.unrealized_pl),"weight":str(self.weight),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:return cls(**dict(value))
  except PortfolioValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise PortfolioValidationError("invalid portfolio position") from exc
@dataclass(frozen=True,slots=True)
class PortfolioSnapshot:
 account_id:str;cash:Decimal;buying_power:Decimal;equity:Decimal;market_value:Decimal;total_value:Decimal;positions:tuple[PortfolioPosition,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"account_id",_text(self.account_id,"account_id"))
  for name in ("cash","buying_power","equity","market_value","total_value"):object.__setattr__(self,name,_decimal(getattr(self,name),name))
  if self.market_value!=sum((p.market_value for p in self.positions),Decimal("0")):raise PortfolioValidationError("market_value must equal summed position market value")
  if self.total_value!=self.cash+self.market_value:raise PortfolioValidationError("total_value must equal cash plus market_value")
  if not isinstance(self.positions,tuple) or any(not isinstance(p,PortfolioPosition) for p in self.positions):raise PortfolioValidationError("positions must be immutable portfolio position tuple")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"account_id":self.account_id,"cash":str(self.cash),"buying_power":str(self.buying_power),"equity":str(self.equity),"market_value":str(self.market_value),"total_value":str(self.total_value),"positions":[p.to_dict() for p in self.positions],"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["positions"]=tuple(PortfolioPosition.from_dict(p) for p in data["positions"]);return cls(**data)
  except PortfolioValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise PortfolioValidationError("invalid portfolio snapshot") from exc
@dataclass(frozen=True,slots=True)
class PortfolioCriteriaResult:
 name:str;passed:bool;detail:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"name",_text(self.name,"criteria name"));object.__setattr__(self,"detail",_text(self.detail,"criteria detail"))
  if not isinstance(self.passed,bool):raise PortfolioValidationError("criteria passed must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"name":self.name,"passed":self.passed,"detail":self.detail,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):return cls(**dict(value))
@dataclass(frozen=True,slots=True)
class PortfolioResult:
 request_id:str;account_id:str;cash:Decimal;buying_power:Decimal;equity:Decimal;market_value:Decimal;total_value:Decimal;positions:tuple[PortfolioPosition,...];decision:PortfolioDecision;criteria_results:tuple[PortfolioCriteriaResult,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"account_id",_text(self.account_id,"account_id"))
  if not isinstance(self.decision,PortfolioDecision):raise PortfolioValidationError("decision must be PortfolioDecision")
  for name in ("cash","buying_power","equity","market_value","total_value"):object.__setattr__(self,name,_decimal(getattr(self,name),name))
  if not isinstance(self.positions,tuple) or any(not isinstance(p,PortfolioPosition) for p in self.positions):raise PortfolioValidationError("positions must be immutable portfolio position tuple")
  if self.decision is PortfolioDecision.SUCCESS:
   PortfolioSnapshot(self.account_id,self.cash,self.buying_power,self.equity,self.market_value,self.total_value,self.positions)
  elif self.positions or any(getattr(self,n)!=0 for n in ("cash","buying_power","equity","market_value","total_value")):raise PortfolioValidationError("failure result cannot expose portfolio values")
  if not isinstance(self.criteria_results,tuple) or any(not isinstance(c,PortfolioCriteriaResult) for c in self.criteria_results):raise PortfolioValidationError("criteria_results must be immutable criteria tuple")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 @property
 def success(self):return self.decision is PortfolioDecision.SUCCESS
 def to_dict(self):return {"request_id":self.request_id,"account_id":self.account_id,"cash":str(self.cash),"buying_power":str(self.buying_power),"equity":str(self.equity),"market_value":str(self.market_value),"total_value":str(self.total_value),"positions":[p.to_dict() for p in self.positions],"decision":self.decision.value,"criteria_results":[c.to_dict() for c in self.criteria_results],"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["decision"]=PortfolioDecision(data["decision"]);data["positions"]=tuple(PortfolioPosition.from_dict(p) for p in data["positions"]);data["criteria_results"]=tuple(PortfolioCriteriaResult.from_dict(c) for c in data["criteria_results"]);return cls(**data)
  except PortfolioValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise PortfolioValidationError("invalid portfolio result") from exc
