from __future__ import annotations
from dataclasses import dataclass,field
from decimal import Decimal
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
@dataclass(frozen=True,slots=True)
class ExecutionJournalPolicy:
    version:str="execution_journal_policy_v1";fsync_enabled:bool=True;reject_duplicates:bool=True;verify_on_load:bool=True;allow_empty_journal:bool=True;maximum_record_bytes:int=1_048_576;locking_enabled:bool=True;lock_timeout_seconds:Decimal=Decimal("10");lock_poll_interval_seconds:Decimal=Decimal("0.05");lock_stale_after_seconds:Decimal=Decimal("300");lock_file_suffix:str=".lock";metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.version,str) or not self.version.strip():raise ValueError("version must be nonempty")
        object.__setattr__(self,"version",self.version.strip())
        for n in ("fsync_enabled","reject_duplicates","verify_on_load","allow_empty_journal","locking_enabled"):
            if not isinstance(getattr(self,n),bool):raise ValueError(f"{n} must be boolean")
        if isinstance(self.maximum_record_bytes,bool) or not isinstance(self.maximum_record_bytes,int) or self.maximum_record_bytes<=0:raise ValueError("maximum_record_bytes must be positive integer")
        for n in ("lock_timeout_seconds","lock_poll_interval_seconds","lock_stale_after_seconds"):
            try:v=Decimal(getattr(self,n))
            except (ValueError,TypeError) as e:raise ValueError(f"{n} must be Decimal-compatible") from e
            if not v.is_finite() or (n=="lock_timeout_seconds" and v<0) or (n!="lock_timeout_seconds" and v<=0):raise ValueError(f"{n} has invalid duration")
            object.__setattr__(self,n,v)
        if not isinstance(self.lock_file_suffix,str) or not self.lock_file_suffix or "/" in self.lock_file_suffix or "\\" in self.lock_file_suffix:raise ValueError("lock_file_suffix must be nonempty without path separators")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"version":self.version,"fsync_enabled":self.fsync_enabled,"reject_duplicates":self.reject_duplicates,"verify_on_load":self.verify_on_load,"allow_empty_journal":self.allow_empty_journal,"maximum_record_bytes":self.maximum_record_bytes,"locking_enabled":self.locking_enabled,"lock_timeout_seconds":str(self.lock_timeout_seconds),"lock_poll_interval_seconds":str(self.lock_poll_interval_seconds),"lock_stale_after_seconds":str(self.lock_stale_after_seconds),"lock_file_suffix":self.lock_file_suffix,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        try:return cls(**dict(v))
        except (TypeError,ValueError,KeyError) as e:raise ValueError("Unable to deserialize execution journal policy") from e
