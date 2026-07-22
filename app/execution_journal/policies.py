from __future__ import annotations
from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
@dataclass(frozen=True,slots=True)
class ExecutionJournalPolicy:
    version:str="execution_journal_policy_v1";fsync_enabled:bool=True;reject_duplicates:bool=True;verify_on_load:bool=True;allow_empty_journal:bool=True;maximum_record_bytes:int=1_048_576;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.version,str) or not self.version.strip():raise ValueError("version must be nonempty")
        object.__setattr__(self,"version",self.version.strip())
        for n in ("fsync_enabled","reject_duplicates","verify_on_load","allow_empty_journal"):
            if not isinstance(getattr(self,n),bool):raise ValueError(f"{n} must be boolean")
        if isinstance(self.maximum_record_bytes,bool) or not isinstance(self.maximum_record_bytes,int) or self.maximum_record_bytes<=0:raise ValueError("maximum_record_bytes must be positive integer")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"version":self.version,"fsync_enabled":self.fsync_enabled,"reject_duplicates":self.reject_duplicates,"verify_on_load":self.verify_on_load,"allow_empty_journal":self.allow_empty_journal,"maximum_record_bytes":self.maximum_record_bytes,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        try:return cls(**dict(v))
        except (TypeError,ValueError,KeyError) as e:raise ValueError("Unable to deserialize execution journal policy") from e
