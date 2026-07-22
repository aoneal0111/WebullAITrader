from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.analytics.exceptions import AnalyticsValidationError
@dataclass(frozen=True,slots=True)
class AnalyticsPolicy:
    version:str="analytics_policy_v1";enabled:bool=False;strict_validation:bool=True;include_equity_curve:bool=True;include_drawdown_curve:bool=True;include_diagnostics:bool=True;require_active_journal:bool=True;require_entries:bool=False;classify_zero_realized_profit_loss_as_breakeven:bool=True;minimum_classified_trades:int|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.version,str) or not self.version.strip() or self.version!=self.version.strip():raise AnalyticsValidationError("version must be a non-empty stripped string")
        for n in ("enabled","strict_validation","include_equity_curve","include_drawdown_curve","include_diagnostics","require_active_journal","require_entries","classify_zero_realized_profit_loss_as_breakeven"):
            if not isinstance(getattr(self,n),bool):raise AnalyticsValidationError("policy flags must be boolean")
        if self.minimum_classified_trades is not None and (isinstance(self.minimum_classified_trades,bool) or not isinstance(self.minimum_classified_trades,int) or self.minimum_classified_trades<1):raise AnalyticsValidationError("minimum_classified_trades must be a positive integer or None")
        if self.include_drawdown_curve and not self.include_equity_curve:raise AnalyticsValidationError("drawdown curve requires equity curve")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {n:(thaw_json_value(getattr(self,n)) if n=="metadata" else getattr(self,n)) for n in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls,v):
        try:return cls(**dict(v))
        except AnalyticsValidationError:raise
        except (TypeError,ValueError) as exc:raise AnalyticsValidationError("invalid analytics policy") from exc
