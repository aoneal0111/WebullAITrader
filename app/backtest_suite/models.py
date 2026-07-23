"""Immutable structural coordination records for ordered backtest suites."""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from app.backtest_run import BacktestRunRequest,BacktestRunResult
from app.backtest_report import BacktestReportPolicy,BacktestReportResult
from app.backtest_suite.exceptions import BacktestSuiteValidationError
def _text(v,n,optional=False):
    if optional and v is None:return None
    if not isinstance(v,str) or not v.strip() or v!=v.strip():raise BacktestSuiteValidationError(f"{n} must be a non-empty stripped string")
    return v
def _time(v,n):
    if not isinstance(v,datetime) or v.tzinfo is None or v.utcoffset() is None:raise BacktestSuiteValidationError(f"{n} must be timezone-aware")
    return v
def _strings(v,n):
    if not isinstance(v,tuple) or any(not isinstance(x,str) or not x.strip() for x in v):raise BacktestSuiteValidationError(f"{n} must be immutable strings")
    return v
class BacktestSuiteStatus(StrEnum):COMPLETED="COMPLETED";PARTIALLY_COMPLETED="PARTIALLY_COMPLETED";EMPTY="EMPTY";DISABLED="DISABLED";REJECTED="REJECTED";FAILED="FAILED"
class BacktestSuiteItemStatus(StrEnum):COMPLETED="COMPLETED";RUN_REJECTED="RUN_REJECTED";RUN_FAILED="RUN_FAILED";REPORT_REJECTED="REPORT_REJECTED";REPORT_FAILED="REPORT_FAILED";SKIPPED="SKIPPED"
@dataclass(frozen=True,slots=True)
class BacktestSuitePolicy:
    enabled:bool=True;fail_fast:bool=False
    def __post_init__(self):
        if not isinstance(self.enabled,bool) or not isinstance(self.fail_fast,bool):raise BacktestSuiteValidationError("policy flags must be boolean")
    def to_dict(self):return {"enabled":self.enabled,"fail_fast":self.fail_fast}
    @classmethod
    def from_dict(cls,v):return cls(v.get("enabled",True),v.get("fail_fast",False))
@dataclass(frozen=True,slots=True)
class BacktestSuiteIdentity:
    suite_id:str
    def __post_init__(self):object.__setattr__(self,"suite_id",_text(self.suite_id,"suite_id"))
    def to_dict(self):return {"suite_id":self.suite_id}
@dataclass(frozen=True,slots=True)
class BacktestSuiteItemIdentity:
    item_id:str;run_id:str;report_id:str
    def __post_init__(self):
        for n in self.__dataclass_fields__:object.__setattr__(self,n,_text(getattr(self,n),n))
    def to_dict(self):return {n:getattr(self,n) for n in self.__dataclass_fields__}
@dataclass(frozen=True,slots=True)
class BacktestSuiteItemRequest:
    identity:BacktestSuiteItemIdentity;run_request:BacktestRunRequest;report_policy:BacktestReportPolicy;report_requested_at:datetime
    def __post_init__(self):
        if not isinstance(self.identity,BacktestSuiteItemIdentity) or not isinstance(self.run_request,BacktestRunRequest) or not isinstance(self.report_policy,BacktestReportPolicy):raise BacktestSuiteValidationError("suite item contracts are invalid")
        object.__setattr__(self,"report_requested_at",_time(self.report_requested_at,"report_requested_at"))
    def to_dict(self):return {"identity":self.identity.to_dict(),"run_request":self.run_request.to_dict(),"report_policy":self.report_policy.to_dict(),"report_requested_at":self.report_requested_at.isoformat()}
@dataclass(frozen=True,slots=True)
class BacktestSuiteRequest:
    identity:BacktestSuiteIdentity;items:tuple[BacktestSuiteItemRequest,...];policy:BacktestSuitePolicy;requested_at:datetime;completed_at:datetime
    def __post_init__(self):
        if not isinstance(self.identity,BacktestSuiteIdentity) or not isinstance(self.items,tuple) or any(not isinstance(x,BacktestSuiteItemRequest) for x in self.items) or not isinstance(self.policy,BacktestSuitePolicy):raise BacktestSuiteValidationError("suite request contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if self.completed_at<self.requested_at:raise BacktestSuiteValidationError("completed_at cannot precede requested_at")
    def to_dict(self):return {"identity":self.identity.to_dict(),"items":[x.to_dict() for x in self.items],"policy":self.policy.to_dict(),"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat()}
@dataclass(frozen=True,slots=True)
class BacktestSuiteCriteriaResult:
    accepted:bool;errors:tuple[str,...]=()
    def __post_init__(self):
        if not isinstance(self.accepted,bool):raise BacktestSuiteValidationError("accepted must be boolean")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"))
    def to_dict(self):return {"accepted":self.accepted,"errors":list(self.errors)}
@dataclass(frozen=True,slots=True)
class BacktestSuiteItemRecord:
    index:int;identity:BacktestSuiteItemIdentity;status:BacktestSuiteItemStatus;run_request:BacktestRunRequest;run_result:BacktestRunResult|None;report_result:BacktestReportResult|None;error_type:str|None=None;message:str|None=None
    def __post_init__(self):
        if isinstance(self.index,bool) or not isinstance(self.index,int) or self.index<0:raise BacktestSuiteValidationError("index must be non-negative integer")
        if not isinstance(self.identity,BacktestSuiteItemIdentity) or not isinstance(self.status,BacktestSuiteItemStatus) or not isinstance(self.run_request,BacktestRunRequest):raise BacktestSuiteValidationError("item record contracts are invalid")
        if self.run_result is not None and not isinstance(self.run_result,BacktestRunResult):raise BacktestSuiteValidationError("run_result is invalid")
        if self.report_result is not None and not isinstance(self.report_result,BacktestReportResult):raise BacktestSuiteValidationError("report_result is invalid")
        object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True));object.__setattr__(self,"message",_text(self.message,"message",True))
    def to_dict(self):return {"index":self.index,"identity":self.identity.to_dict(),"status":self.status.value,"run_request":self.run_request.to_dict(),"run_result":self.run_result.to_dict() if self.run_result else None,"report_result":self.report_result.to_dict() if self.report_result else None,"error_type":self.error_type,"message":self.message}
@dataclass(frozen=True,slots=True)
class BacktestSuiteSummary:
    total_items:int;processed_items:int;completed_items:int;failed_items:int;skipped_items:int
    def __post_init__(self):
        for n in self.__dataclass_fields__:
            if isinstance(getattr(self,n),bool) or not isinstance(getattr(self,n),int) or getattr(self,n)<0:raise BacktestSuiteValidationError("summary counts must be non-negative integers")
        if self.completed_items+self.failed_items+self.skipped_items!=self.total_items or self.processed_items!=self.completed_items+self.failed_items:raise BacktestSuiteValidationError("summary counts are inconsistent")
    def to_dict(self):return {n:getattr(self,n) for n in self.__dataclass_fields__}
@dataclass(frozen=True,slots=True)
class BacktestSuiteResult:
    identity:BacktestSuiteIdentity;status:BacktestSuiteStatus;requested_at:datetime;completed_at:datetime;items:tuple[BacktestSuiteItemRecord,...];summary:BacktestSuiteSummary;criteria:BacktestSuiteCriteriaResult;errors:tuple[str,...]=();error_type:str|None=None
    def __post_init__(self):
        if not isinstance(self.identity,BacktestSuiteIdentity) or not isinstance(self.status,BacktestSuiteStatus) or not isinstance(self.summary,BacktestSuiteSummary) or not isinstance(self.criteria,BacktestSuiteCriteriaResult):raise BacktestSuiteValidationError("suite result contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if not isinstance(self.items,tuple) or any(not isinstance(x,BacktestSuiteItemRecord) for x in self.items):raise BacktestSuiteValidationError("result items must be immutable")
        if self.items and tuple(x.index for x in self.items)!=tuple(range(len(self.items))):raise BacktestSuiteValidationError("item indexes are not contiguous")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"));object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True))
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"items":[x.to_dict() for x in self.items],"summary":self.summary.to_dict(),"criteria":self.criteria.to_dict(),"errors":list(self.errors),"error_type":self.error_type}
