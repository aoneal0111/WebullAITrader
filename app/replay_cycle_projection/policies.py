from dataclasses import dataclass,field
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.replay_cycle_projection.exceptions import ReplayCycleProjectionValidationError
from app.replay_cycle_projection.models import ReplayCycleProjectionFailureMode
@dataclass(frozen=True,slots=True)
class ReplayCycleProjectionPolicy:
    version:str="replay_cycle_projection_policy_v1";enabled:bool=True;failure_mode:ReplayCycleProjectionFailureMode=ReplayCycleProjectionFailureMode.STOP_ON_FAILURE;allow_empty:bool=False;include_diagnostics:bool=True;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.version,str) or not self.version.strip() or self.version!=self.version.strip():raise ReplayCycleProjectionValidationError("version must be non-empty stripped string")
        if any(not isinstance(getattr(self,n),bool) for n in ("enabled","allow_empty","include_diagnostics")):raise ReplayCycleProjectionValidationError("policy flags must be boolean")
        if not isinstance(self.failure_mode,ReplayCycleProjectionFailureMode):raise ReplayCycleProjectionValidationError("failure_mode is invalid")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"version":self.version,"enabled":self.enabled,"failure_mode":self.failure_mode.value,"allow_empty":self.allow_empty,"include_diagnostics":self.include_diagnostics,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(v.get("version","replay_cycle_projection_policy_v1"),v.get("enabled",True),ReplayCycleProjectionFailureMode(v.get("failure_mode","STOP_ON_FAILURE")),v.get("allow_empty",False),v.get("include_diagnostics",True),v.get("metadata",{}))
