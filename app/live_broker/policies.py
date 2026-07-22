from __future__ import annotations
from dataclasses import dataclass,field
from decimal import Decimal
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.trade_proposals.policies import decimal_value
@dataclass(frozen=True,slots=True)
class LiveExecutionPolicy:
    version:str="live_execution_policy_v1";live_execution_enabled:bool=False;require_runtime_capability:bool=True;require_human_confirmation:bool=True;require_journal_authorization:bool=True;require_valid_journal_integrity:bool=True;reject_previously_executed_authorizations:bool=True;maximum_account_snapshot_age_seconds:int=0;maximum_order_quantity:int=0;maximum_order_notional:Decimal=Decimal("0");maximum_daily_loss:Decimal=Decimal("0");allowed_symbols:tuple[str,...]=();required_environment:str="production-live";metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.version,str) or not self.version.strip():raise ValueError("version must be nonempty")
        object.__setattr__(self,"version",self.version.strip())
        for n in ("live_execution_enabled","require_runtime_capability","require_human_confirmation","require_journal_authorization","require_valid_journal_integrity","reject_previously_executed_authorizations"):
            if not isinstance(getattr(self,n),bool):raise ValueError(f"{n} must be boolean")
        for n in ("maximum_account_snapshot_age_seconds","maximum_order_quantity"):
            v=getattr(self,n)
            if isinstance(v,bool) or not isinstance(v,int) or v<0:raise ValueError(f"{n} must be nonnegative integer")
        for n in ("maximum_order_notional","maximum_daily_loss"):
            v=decimal_value(n,getattr(self,n));
            if v<0:raise ValueError(f"{n} must be nonnegative")
            object.__setattr__(self,n,v)
        if not isinstance(self.allowed_symbols,(tuple,list)):raise ValueError("allowed_symbols must be sequence")
        symbols=tuple(x.strip().upper() if isinstance(x,str) else "" for x in self.allowed_symbols)
        if any(not x for x in symbols) or len(symbols)!=len(set(symbols)):raise ValueError("allowed_symbols must be normalized unique symbols")
        object.__setattr__(self,"allowed_symbols",symbols)
        if not isinstance(self.required_environment,str) or not self.required_environment.strip():raise ValueError("required_environment must be nonempty")
        object.__setattr__(self,"required_environment",self.required_environment.strip());object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"version":self.version,"live_execution_enabled":self.live_execution_enabled,"require_runtime_capability":self.require_runtime_capability,"require_human_confirmation":self.require_human_confirmation,"require_journal_authorization":self.require_journal_authorization,"require_valid_journal_integrity":self.require_valid_journal_integrity,"reject_previously_executed_authorizations":self.reject_previously_executed_authorizations,"maximum_account_snapshot_age_seconds":self.maximum_account_snapshot_age_seconds,"maximum_order_quantity":self.maximum_order_quantity,"maximum_order_notional":str(self.maximum_order_notional),"maximum_daily_loss":str(self.maximum_daily_loss),"allowed_symbols":list(self.allowed_symbols),"required_environment":self.required_environment,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        try:return cls(**dict(v))
        except (TypeError,ValueError,KeyError) as e:raise ValueError("Unable to deserialize live execution policy") from e
