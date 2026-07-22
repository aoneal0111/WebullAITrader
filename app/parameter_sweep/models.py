"""Immutable records for ordered caller-defined Backtest Suite cases."""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from app.backtest_suite import BacktestSuiteRequest,BacktestSuiteResult
from app.parameter_sweep.exceptions import ParameterSweepValidationError
def _text(v,n,optional=False):
    if optional and v is None:return None
    if not isinstance(v,str) or not v.strip() or v!=v.strip():raise ParameterSweepValidationError(f"{n} must be a non-empty stripped string")
    return v
def _time(v,n):
    if not isinstance(v,datetime) or v.tzinfo is None or v.utcoffset() is None:raise ParameterSweepValidationError(f"{n} must be timezone-aware")
    return v
def _strings(v,n):
    if not isinstance(v,tuple) or any(not isinstance(x,str) or not x.strip() for x in v):raise ParameterSweepValidationError(f"{n} must be immutable strings")
    return v
class ParameterSweepStatus(StrEnum):COMPLETED="COMPLETED";PARTIALLY_COMPLETED="PARTIALLY_COMPLETED";EMPTY="EMPTY";DISABLED="DISABLED";REJECTED="REJECTED";FAILED="FAILED"
class ParameterSweepCaseStatus(StrEnum):COMPLETED="COMPLETED";SUITE_FAILED="SUITE_FAILED";SKIPPED="SKIPPED"
@dataclass(frozen=True,slots=True)
class ParameterSweepPolicy:
    enabled:bool=True;fail_fast:bool=False
    def __post_init__(self):
        if not isinstance(self.enabled,bool) or not isinstance(self.fail_fast,bool):raise ParameterSweepValidationError("policy flags must be boolean")
    def to_dict(self):return {"enabled":self.enabled,"fail_fast":self.fail_fast}
    @classmethod
    def from_dict(cls,v):return cls(v.get("enabled",True),v.get("fail_fast",False))
@dataclass(frozen=True,slots=True)
class ParameterSweepIdentity:
    sweep_id:str
    def __post_init__(self):object.__setattr__(self,"sweep_id",_text(self.sweep_id,"sweep_id"))
    def to_dict(self):return {"sweep_id":self.sweep_id}
@dataclass(frozen=True,slots=True)
class ParameterSweepCaseIdentity:
    case_id:str;suite_id:str
    def __post_init__(self):object.__setattr__(self,"case_id",_text(self.case_id,"case_id"));object.__setattr__(self,"suite_id",_text(self.suite_id,"suite_id"))
    def to_dict(self):return {"case_id":self.case_id,"suite_id":self.suite_id}
@dataclass(frozen=True,slots=True)
class ParameterSweepCaseRequest:
    identity:ParameterSweepCaseIdentity;suite_request:BacktestSuiteRequest
    def __post_init__(self):
        if not isinstance(self.identity,ParameterSweepCaseIdentity) or not isinstance(self.suite_request,BacktestSuiteRequest):raise ParameterSweepValidationError("case contracts are invalid")
    def to_dict(self):return {"identity":self.identity.to_dict(),"suite_request":self.suite_request.to_dict()}
@dataclass(frozen=True,slots=True)
class ParameterSweepRequest:
    identity:ParameterSweepIdentity;cases:tuple[ParameterSweepCaseRequest,...];policy:ParameterSweepPolicy;requested_at:datetime;completed_at:datetime
    def __post_init__(self):
        if not isinstance(self.identity,ParameterSweepIdentity) or not isinstance(self.cases,tuple) or any(not isinstance(x,ParameterSweepCaseRequest) for x in self.cases) or not isinstance(self.policy,ParameterSweepPolicy):raise ParameterSweepValidationError("sweep request contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if self.completed_at<self.requested_at:raise ParameterSweepValidationError("completed_at cannot precede requested_at")
    def to_dict(self):return {"identity":self.identity.to_dict(),"cases":[x.to_dict() for x in self.cases],"policy":self.policy.to_dict(),"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat()}
@dataclass(frozen=True,slots=True)
class ParameterSweepCriteriaResult:
    accepted:bool;errors:tuple[str,...]=()
    def __post_init__(self):
        if not isinstance(self.accepted,bool):raise ParameterSweepValidationError("accepted must be boolean")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"))
    def to_dict(self):return {"accepted":self.accepted,"errors":list(self.errors)}
@dataclass(frozen=True,slots=True)
class ParameterSweepCaseRecord:
    index:int;identity:ParameterSweepCaseIdentity;status:ParameterSweepCaseStatus;suite_request:BacktestSuiteRequest;suite_result:BacktestSuiteResult|None;error_type:str|None=None;message:str|None=None
    def __post_init__(self):
        if isinstance(self.index,bool) or not isinstance(self.index,int) or self.index<0:raise ParameterSweepValidationError("index must be non-negative integer")
        if not isinstance(self.identity,ParameterSweepCaseIdentity) or not isinstance(self.status,ParameterSweepCaseStatus) or not isinstance(self.suite_request,BacktestSuiteRequest):raise ParameterSweepValidationError("case record contracts are invalid")
        if self.suite_result is not None and not isinstance(self.suite_result,BacktestSuiteResult):raise ParameterSweepValidationError("suite_result is invalid")
        object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True));object.__setattr__(self,"message",_text(self.message,"message",True))
    def to_dict(self):return {"index":self.index,"identity":self.identity.to_dict(),"status":self.status.value,"suite_request":self.suite_request.to_dict(),"suite_result":self.suite_result.to_dict() if self.suite_result else None,"error_type":self.error_type,"message":self.message}
@dataclass(frozen=True,slots=True)
class ParameterSweepSummary:
    total_cases:int;processed_cases:int;completed_cases:int;failed_cases:int;skipped_cases:int
    def __post_init__(self):
        for n in self.__dataclass_fields__:
            if isinstance(getattr(self,n),bool) or not isinstance(getattr(self,n),int) or getattr(self,n)<0:raise ParameterSweepValidationError("summary counts must be non-negative integers")
        if self.completed_cases+self.failed_cases+self.skipped_cases!=self.total_cases or self.processed_cases!=self.completed_cases+self.failed_cases:raise ParameterSweepValidationError("summary counts are inconsistent")
    def to_dict(self):return {n:getattr(self,n) for n in self.__dataclass_fields__}
@dataclass(frozen=True,slots=True)
class ParameterSweepResult:
    identity:ParameterSweepIdentity;status:ParameterSweepStatus;requested_at:datetime;completed_at:datetime;cases:tuple[ParameterSweepCaseRecord,...];summary:ParameterSweepSummary;criteria:ParameterSweepCriteriaResult;errors:tuple[str,...]=();error_type:str|None=None
    def __post_init__(self):
        if not isinstance(self.identity,ParameterSweepIdentity) or not isinstance(self.status,ParameterSweepStatus) or not isinstance(self.summary,ParameterSweepSummary) or not isinstance(self.criteria,ParameterSweepCriteriaResult):raise ParameterSweepValidationError("sweep result contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if not isinstance(self.cases,tuple) or any(not isinstance(x,ParameterSweepCaseRecord) for x in self.cases):raise ParameterSweepValidationError("case records must be immutable")
        if self.cases and tuple(x.index for x in self.cases)!=tuple(range(len(self.cases))):raise ParameterSweepValidationError("case record indexes are invalid")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"));object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True))
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"cases":[x.to_dict() for x in self.cases],"summary":self.summary.to_dict(),"criteria":self.criteria.to_dict(),"errors":list(self.errors),"error_type":self.error_type}
