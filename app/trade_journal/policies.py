from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.trade_journal.exceptions import TradeJournalValidationError
@dataclass(frozen=True,slots=True)
class TradeJournalPolicy:
    version:str="trade_journal_policy_v1";enabled:bool=False;strict_validation:bool=True;allow_duplicate_cycle_ids:bool=False;require_completed_cycle:bool=True;include_summary:bool=True;include_diagnostics:bool=True;maximum_entries:int=10000;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.version,str) or not self.version.strip() or self.version!=self.version.strip():raise TradeJournalValidationError("version must be a non-empty stripped string")
        for n in ("enabled","strict_validation","allow_duplicate_cycle_ids","require_completed_cycle","include_summary","include_diagnostics"):
            if not isinstance(getattr(self,n),bool):raise TradeJournalValidationError("policy flags must be boolean")
        if isinstance(self.maximum_entries,bool) or not isinstance(self.maximum_entries,int) or self.maximum_entries<=0:raise TradeJournalValidationError("maximum_entries must be a positive integer")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {n:(thaw_json_value(getattr(self,n)) if n=="metadata" else getattr(self,n)) for n in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls,v):
        try:return cls(**dict(v))
        except TradeJournalValidationError:raise
        except (TypeError,ValueError) as exc:raise TradeJournalValidationError("invalid trade journal policy") from exc
