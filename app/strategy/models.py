from dataclasses import dataclass,field
from decimal import Decimal,InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.portfolio import PortfolioSnapshot
from app.strategy.exceptions import StrategyValidationError
def _text(value,name):
 if not isinstance(value,str) or not value.strip() or value!=value.strip():raise StrategyValidationError(f"{name} must be a non-empty stripped string")
 return value
def _decimal(value,name):
 if isinstance(value,bool) or not isinstance(value,(Decimal,str,int)):raise StrategyValidationError(f"{name} must be Decimal-compatible")
 try:result=Decimal(value)
 except (InvalidOperation,ValueError) as exc:raise StrategyValidationError(f"{name} must be finite") from exc
 if not result.is_finite():raise StrategyValidationError(f"{name} must be finite")
 return result
class StrategySignal(StrEnum):BUY="BUY";SELL="SELL";HOLD="HOLD";EXIT="EXIT"
@dataclass(frozen=True,slots=True)
class StrategyContext:
 context_id:str;portfolio:PortfolioSnapshot;configuration:Mapping[str,JSONValue];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"context_id",_text(self.context_id,"context_id"))
  if not isinstance(self.portfolio,PortfolioSnapshot):raise StrategyValidationError("portfolio must be PortfolioSnapshot")
  object.__setattr__(self,"configuration",freeze_json_mapping("configuration",self.configuration));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"context_id":self.context_id,"portfolio":self.portfolio.to_dict(),"configuration":thaw_json_value(self.configuration),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["portfolio"]=PortfolioSnapshot.from_dict(data["portfolio"]);return cls(**data)
  except StrategyValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise StrategyValidationError("invalid strategy context") from exc
@dataclass(frozen=True,slots=True)
class StrategyDecision:
 symbol:str;signal:StrategySignal;confidence:Decimal;reasons:tuple[str,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"symbol",_text(self.symbol,"symbol").upper())
  if not isinstance(self.signal,StrategySignal):raise StrategyValidationError("signal must be StrategySignal")
  confidence=_decimal(self.confidence,"confidence")
  if not Decimal("0")<=confidence<=Decimal("1"):raise StrategyValidationError("confidence must be between zero and one")
  object.__setattr__(self,"confidence",confidence)
  if not isinstance(self.reasons,tuple) or not self.reasons or any(not isinstance(x,str) or not x.strip() or x!=x.strip() for x in self.reasons):raise StrategyValidationError("reasons must be a non-empty immutable string tuple")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"symbol":self.symbol,"signal":self.signal.value,"confidence":str(self.confidence),"reasons":list(self.reasons),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["signal"]=StrategySignal(data["signal"]);data["reasons"]=tuple(data["reasons"]);return cls(**data)
  except StrategyValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise StrategyValidationError("invalid strategy decision") from exc
@dataclass(frozen=True,slots=True)
class StrategyResult:
 context_id:str;evaluated:bool;decisions:tuple[StrategyDecision,...];policy_version:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"context_id",_text(self.context_id,"context_id"));object.__setattr__(self,"policy_version",_text(self.policy_version,"policy_version"))
  if not isinstance(self.evaluated,bool):raise StrategyValidationError("evaluated must be boolean")
  if not isinstance(self.decisions,tuple) or any(not isinstance(x,StrategyDecision) for x in self.decisions):raise StrategyValidationError("decisions must be an immutable strategy decision tuple")
  if not self.evaluated and self.decisions:raise StrategyValidationError("unevaluated result cannot contain decisions")
  symbols=tuple(x.symbol for x in self.decisions)
  if len(set(symbols))!=len(symbols):raise StrategyValidationError("strategy decisions must have unique symbols")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"context_id":self.context_id,"evaluated":self.evaluated,"decisions":[x.to_dict() for x in self.decisions],"policy_version":self.policy_version,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["decisions"]=tuple(StrategyDecision.from_dict(x) for x in data["decisions"]);return cls(**data)
  except StrategyValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise StrategyValidationError("invalid strategy result") from exc
