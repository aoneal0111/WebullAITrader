from dataclasses import dataclass, field
from typing import Mapping
from app.committee.models import JSONValue, freeze_json_mapping, thaw_json_value
from app.trading_cycle.exceptions import TradingCycleValidationError

@dataclass(frozen=True, slots=True)
class TradingCyclePolicy:
    version:str="trading_cycle_policy_v1"; enabled:bool=False; strict_validation:bool=True
    include_stage_records:bool=True; include_decision_trace:bool=True; include_diagnostics:bool=True; include_metrics:bool=True
    require_portfolio_before:bool=True; require_completed_timing:bool=True; require_identity_continuity:bool=True
    metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.version,str) or not self.version.strip() or self.version!=self.version.strip(): raise TradingCycleValidationError("version must be a non-empty stripped string")
        for name in ("enabled","strict_validation","include_stage_records","include_decision_trace","include_diagnostics","include_metrics","require_portfolio_before","require_completed_timing","require_identity_continuity"):
            if not isinstance(getattr(self,name),bool): raise TradingCycleValidationError("policy flags must be boolean")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self): return {name:(thaw_json_value(value) if name=="metadata" else value) for name,value in ((f,getattr(self,f)) for f in self.__dataclass_fields__)}
    @classmethod
    def from_dict(cls,value):
        try:return cls(**dict(value))
        except TradingCycleValidationError:raise
        except (TypeError,ValueError) as exc:raise TradingCycleValidationError("invalid trading cycle policy") from exc
