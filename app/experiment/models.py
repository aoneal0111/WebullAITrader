"""Immutable records for deterministic coordination of parameter sweeps."""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from app.parameter_sweep import ParameterSweepRequest,ParameterSweepResult
from app.experiment.exceptions import ExperimentValidationError

def _text(value,name,optional=False):
    if optional and value is None:return None
    if not isinstance(value,str) or not value.strip() or value!=value.strip():raise ExperimentValidationError(f"{name} must be a non-empty stripped string")
    return value
def _time(value,name):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:raise ExperimentValidationError(f"{name} must be timezone-aware")
    return value
def _strings(value,name):
    if not isinstance(value,tuple) or any(not isinstance(x,str) or not x.strip() for x in value):raise ExperimentValidationError(f"{name} must be immutable strings")
    return value

class ExperimentStatus(StrEnum):
    COMPLETED="COMPLETED";PARTIALLY_COMPLETED="PARTIALLY_COMPLETED";EMPTY="EMPTY";DISABLED="DISABLED";REJECTED="REJECTED";FAILED="FAILED"
class ExperimentSweepStatus(StrEnum):
    COMPLETED="COMPLETED";SWEEP_FAILED="SWEEP_FAILED";SKIPPED="SKIPPED"

@dataclass(frozen=True,slots=True)
class ExperimentPolicy:
    enabled:bool=True;fail_fast:bool=False
    def __post_init__(self):
        if not isinstance(self.enabled,bool) or not isinstance(self.fail_fast,bool):raise ExperimentValidationError("policy flags must be boolean")
    def to_dict(self):return {"enabled":self.enabled,"fail_fast":self.fail_fast}
    @classmethod
    def from_dict(cls,value):return cls(value.get("enabled",True),value.get("fail_fast",False))
@dataclass(frozen=True,slots=True)
class ExperimentIdentity:
    experiment_id:str
    def __post_init__(self):object.__setattr__(self,"experiment_id",_text(self.experiment_id,"experiment_id"))
    def to_dict(self):return {"experiment_id":self.experiment_id}
@dataclass(frozen=True,slots=True)
class ExperimentSweepIdentity:
    sweep_entry_id:str;parameter_sweep_id:str
    def __post_init__(self):
        object.__setattr__(self,"sweep_entry_id",_text(self.sweep_entry_id,"sweep_entry_id"));object.__setattr__(self,"parameter_sweep_id",_text(self.parameter_sweep_id,"parameter_sweep_id"))
    def to_dict(self):return {"sweep_entry_id":self.sweep_entry_id,"parameter_sweep_id":self.parameter_sweep_id}
@dataclass(frozen=True,slots=True)
class ExperimentSweepRequest:
    identity:ExperimentSweepIdentity;parameter_sweep_request:ParameterSweepRequest
    def __post_init__(self):
        if not isinstance(self.identity,ExperimentSweepIdentity) or not isinstance(self.parameter_sweep_request,ParameterSweepRequest):raise ExperimentValidationError("experiment sweep contracts are invalid")
    def to_dict(self):return {"identity":self.identity.to_dict(),"parameter_sweep_request":self.parameter_sweep_request.to_dict()}
@dataclass(frozen=True,slots=True)
class ExperimentRequest:
    identity:ExperimentIdentity;sweeps:tuple[ExperimentSweepRequest,...];policy:ExperimentPolicy;requested_at:datetime;completed_at:datetime
    def __post_init__(self):
        if not isinstance(self.identity,ExperimentIdentity) or not isinstance(self.sweeps,tuple) or any(not isinstance(x,ExperimentSweepRequest) for x in self.sweeps) or not isinstance(self.policy,ExperimentPolicy):raise ExperimentValidationError("experiment request contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if self.completed_at<self.requested_at:raise ExperimentValidationError("completed_at cannot precede requested_at")
    def to_dict(self):return {"identity":self.identity.to_dict(),"sweeps":[x.to_dict() for x in self.sweeps],"policy":self.policy.to_dict(),"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat()}
@dataclass(frozen=True,slots=True)
class ExperimentCriteriaResult:
    accepted:bool;errors:tuple[str,...]=()
    def __post_init__(self):
        if not isinstance(self.accepted,bool):raise ExperimentValidationError("accepted must be boolean")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"))
    def to_dict(self):return {"accepted":self.accepted,"errors":list(self.errors)}
@dataclass(frozen=True,slots=True)
class ExperimentSweepRecord:
    index:int;identity:ExperimentSweepIdentity;status:ExperimentSweepStatus;parameter_sweep_request:ParameterSweepRequest;parameter_sweep_result:ParameterSweepResult|None;error_type:str|None=None;message:str|None=None
    def __post_init__(self):
        if isinstance(self.index,bool) or not isinstance(self.index,int) or self.index<0:raise ExperimentValidationError("index must be non-negative integer")
        if not isinstance(self.identity,ExperimentSweepIdentity) or not isinstance(self.status,ExperimentSweepStatus) or not isinstance(self.parameter_sweep_request,ParameterSweepRequest):raise ExperimentValidationError("sweep record contracts are invalid")
        if self.parameter_sweep_result is not None and not isinstance(self.parameter_sweep_result,ParameterSweepResult):raise ExperimentValidationError("parameter_sweep_result is invalid")
        object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True));object.__setattr__(self,"message",_text(self.message,"message",True))
    def to_dict(self):return {"index":self.index,"identity":self.identity.to_dict(),"status":self.status.value,"parameter_sweep_request":self.parameter_sweep_request.to_dict(),"parameter_sweep_result":self.parameter_sweep_result.to_dict() if self.parameter_sweep_result else None,"error_type":self.error_type,"message":self.message}
@dataclass(frozen=True,slots=True)
class ExperimentSummary:
    total_sweeps:int;processed_sweeps:int;completed_sweeps:int;failed_sweeps:int;skipped_sweeps:int
    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,int) or value<0:raise ExperimentValidationError("summary counts must be non-negative integers")
        if self.completed_sweeps+self.failed_sweeps+self.skipped_sweeps!=self.total_sweeps or self.processed_sweeps!=self.completed_sweeps+self.failed_sweeps:raise ExperimentValidationError("summary counts are inconsistent")
    def to_dict(self):return {name:getattr(self,name) for name in self.__dataclass_fields__}
@dataclass(frozen=True,slots=True)
class ExperimentResult:
    identity:ExperimentIdentity;status:ExperimentStatus;requested_at:datetime;completed_at:datetime;sweeps:tuple[ExperimentSweepRecord,...];summary:ExperimentSummary;criteria:ExperimentCriteriaResult;errors:tuple[str,...]=();error_type:str|None=None
    def __post_init__(self):
        if not isinstance(self.identity,ExperimentIdentity) or not isinstance(self.status,ExperimentStatus) or not isinstance(self.summary,ExperimentSummary) or not isinstance(self.criteria,ExperimentCriteriaResult):raise ExperimentValidationError("experiment result contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if not isinstance(self.sweeps,tuple) or any(not isinstance(x,ExperimentSweepRecord) for x in self.sweeps):raise ExperimentValidationError("sweep records must be immutable")
        if self.sweeps and tuple(x.index for x in self.sweeps)!=tuple(range(len(self.sweeps))):raise ExperimentValidationError("sweep record indexes are invalid")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"));object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True))
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"sweeps":[x.to_dict() for x in self.sweeps],"summary":self.summary.to_dict(),"criteria":self.criteria.to_dict(),"errors":list(self.errors),"error_type":self.error_type}
