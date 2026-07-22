from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping
from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.trade_proposals.policies import decimal_value

@dataclass(frozen=True, slots=True)
class ExecutionSafetyPolicy:
    version: str = "execution_safety_policy_v1"
    kill_switch_active: bool = True
    live_mode_enabled: bool = False
    require_human_authorization: bool = True
    maximum_order_quantity: int = 0
    maximum_order_notional: Decimal = Decimal("0")
    maximum_symbol_position: Decimal = Decimal("0")
    maximum_daily_loss: Decimal = Decimal("0")
    duplicate_window_seconds: int = 0
    allowed_symbols: tuple[str, ...] = ()
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.version,str) or not self.version.strip(): raise ValueError("version must be nonempty")
        object.__setattr__(self,"version",self.version.strip())
        for n in ("kill_switch_active","live_mode_enabled","require_human_authorization"):
            if not isinstance(getattr(self,n),bool): raise ValueError(f"{n} must be a boolean")
        for n in ("maximum_order_quantity","duplicate_window_seconds"):
            v=getattr(self,n)
            if isinstance(v,bool) or not isinstance(v,int) or v<0: raise ValueError(f"{n} must be a nonnegative integer")
        for n in ("maximum_order_notional","maximum_symbol_position","maximum_daily_loss"):
            v=decimal_value(n,getattr(self,n))
            if v<0: raise ValueError(f"{n} must be nonnegative")
            object.__setattr__(self,n,v)
        if not isinstance(self.allowed_symbols,(tuple,list)): raise ValueError("allowed_symbols must be a sequence")
        symbols=tuple(x.strip().upper() if isinstance(x,str) else "" for x in self.allowed_symbols)
        if any(not x for x in symbols) or len(set(symbols))!=len(symbols): raise ValueError("allowed_symbols must be normalized and unique")
        object.__setattr__(self,"allowed_symbols",symbols)
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):
        return {"version":self.version,"kill_switch_active":self.kill_switch_active,"live_mode_enabled":self.live_mode_enabled,
          "require_human_authorization":self.require_human_authorization,"maximum_order_quantity":self.maximum_order_quantity,
          "maximum_order_notional":str(self.maximum_order_notional),"maximum_symbol_position":str(self.maximum_symbol_position),
          "maximum_daily_loss":str(self.maximum_daily_loss),"duplicate_window_seconds":self.duplicate_window_seconds,
          "allowed_symbols":list(self.allowed_symbols),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        if not isinstance(v,Mapping): raise ValueError("serialized policy must be a mapping")
        try: return cls(**dict(v))
        except (TypeError,ValueError,KeyError) as e: raise ValueError("Unable to deserialize execution safety policy") from e
