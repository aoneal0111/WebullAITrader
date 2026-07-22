from __future__ import annotations
from dataclasses import dataclass,field
from decimal import Decimal
from typing import Any,Mapping
from app.broker_execution import ExecutionMode
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.trade_proposals.policies import decimal_value

@dataclass(frozen=True,slots=True)
class PaperBrokerPolicy:
    version:str="paper_broker_policy_v1"
    supported_modes:tuple[ExecutionMode,...]=(ExecutionMode.PAPER,)
    immediate_fill:bool=True
    fill_price_adjustment:Decimal=Decimal("0")
    maximum_fill_quantity:int=1_000_000
    allow_partial_fills:bool=False
    metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.version,str) or not self.version.strip():raise ValueError("version must be nonempty")
        object.__setattr__(self,"version",self.version.strip())
        if not isinstance(self.supported_modes,(tuple,list)) or not self.supported_modes:raise ValueError("supported_modes must be nonempty")
        modes=tuple(self.supported_modes)
        if any(not isinstance(x,ExecutionMode) for x in modes) or len(set(modes))!=len(modes):raise ValueError("supported_modes must contain unique ExecutionMode values")
        object.__setattr__(self,"supported_modes",modes)
        for n in ("immediate_fill","allow_partial_fills"):
            if not isinstance(getattr(self,n),bool):raise ValueError(f"{n} must be boolean")
        object.__setattr__(self,"fill_price_adjustment",decimal_value("fill_price_adjustment",self.fill_price_adjustment))
        if isinstance(self.maximum_fill_quantity,bool) or not isinstance(self.maximum_fill_quantity,int) or self.maximum_fill_quantity<=0:raise ValueError("maximum_fill_quantity must be a positive integer")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"version":self.version,"supported_modes":[x.value for x in self.supported_modes],"immediate_fill":self.immediate_fill,"fill_price_adjustment":str(self.fill_price_adjustment),"maximum_fill_quantity":self.maximum_fill_quantity,"allow_partial_fills":self.allow_partial_fills,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        if not isinstance(v,Mapping):raise ValueError("serialized policy must be a mapping")
        try:
            d=dict(v);d["supported_modes"]=tuple(ExecutionMode(x) for x in d["supported_modes"]);return cls(**d)
        except (KeyError,TypeError,ValueError) as e:raise ValueError("Unable to deserialize paper broker policy") from e
