"""Read-only presentation records over an immutable BacktestRunResult."""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from app.analytics import AnalyticsResult
from app.backtest_run import BacktestRunResult,BacktestRunStage,BacktestRunStageStatus,BacktestRunStatus
from app.backtest_report.exceptions import BacktestReportValidationError
def _text(v,n,optional=False):
    if optional and v is None:return None
    if not isinstance(v,str) or not v.strip() or v!=v.strip():raise BacktestReportValidationError(f"{n} must be a non-empty stripped string")
    return v
def _time(v,n):
    if not isinstance(v,datetime) or v.tzinfo is None or v.utcoffset() is None:raise BacktestReportValidationError(f"{n} must be timezone-aware")
    return v
def _strings(v,n):
    if not isinstance(v,tuple) or any(not isinstance(x,str) or not x.strip() for x in v):raise BacktestReportValidationError(f"{n} must be immutable strings")
    return v
class BacktestReportStatus(StrEnum):COMPLETED="COMPLETED";PARTIAL="PARTIAL";EMPTY="EMPTY";DISABLED="DISABLED";REJECTED="REJECTED";FAILED="FAILED"
@dataclass(frozen=True,slots=True)
class BacktestReportPolicy:
    enabled:bool=True;include_stage_history:bool=True;include_warnings:bool=True;include_errors:bool=True
    def __post_init__(self):
        if any(not isinstance(getattr(self,n),bool) for n in self.__dataclass_fields__):raise BacktestReportValidationError("policy flags must be boolean")
    def to_dict(self):return {n:getattr(self,n) for n in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls,v):return cls(**dict(v))
@dataclass(frozen=True,slots=True)
class BacktestReportIdentity:
    report_id:str;run_id:str
    def __post_init__(self):object.__setattr__(self,"report_id",_text(self.report_id,"report_id"));object.__setattr__(self,"run_id",_text(self.run_id,"run_id"))
    def to_dict(self):return {"report_id":self.report_id,"run_id":self.run_id}
@dataclass(frozen=True,slots=True)
class BacktestReportRequest:
    identity:BacktestReportIdentity;run_result:BacktestRunResult;policy:BacktestReportPolicy;requested_at:datetime
    def __post_init__(self):
        if not isinstance(self.identity,BacktestReportIdentity) or not isinstance(self.run_result,BacktestRunResult) or not isinstance(self.policy,BacktestReportPolicy):raise BacktestReportValidationError("request contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"))
    def to_dict(self):return {"identity":self.identity.to_dict(),"run_result":self.run_result.to_dict(),"policy":self.policy.to_dict(),"requested_at":self.requested_at.isoformat()}
@dataclass(frozen=True,slots=True)
class BacktestReportCriteriaResult:
    criterion:str;passed:bool;reasons:tuple[str,...]=()
    def __post_init__(self):object.__setattr__(self,"criterion",_text(self.criterion,"criterion"));isinstance(self.passed,bool) or (_ for _ in ()).throw(BacktestReportValidationError("passed must be boolean"));object.__setattr__(self,"reasons",_strings(self.reasons,"reasons"))
    def to_dict(self):return {"criterion":self.criterion,"passed":self.passed,"reasons":list(self.reasons)}
@dataclass(frozen=True,slots=True)
class BacktestReportOverview:
    report_id:str;run_id:str;source_id:str|None;run_status:BacktestRunStatus;report_status:BacktestReportStatus;stopped_at:BacktestRunStage;run_requested_at:datetime;run_completed_at:datetime;report_requested_at:datetime
    def to_dict(self):return {"report_id":self.report_id,"run_id":self.run_id,"source_id":self.source_id,"run_status":self.run_status.value,"report_status":self.report_status.value,"stopped_at":self.stopped_at.value,"run_requested_at":self.run_requested_at.isoformat(),"run_completed_at":self.run_completed_at.isoformat(),"report_requested_at":self.report_requested_at.isoformat()}
@dataclass(frozen=True,slots=True)
class BacktestReportStageSummary:
    stage:BacktestRunStage;status:BacktestRunStageStatus;message:str|None=None;error_type:str|None=None
    def to_dict(self):return {"stage":self.stage.value,"status":self.status.value,"message":self.message,"error_type":self.error_type}
@dataclass(frozen=True,slots=True)
class BacktestReportActivitySummary:
    replay_event_count:int|None;projected_cycle_count:int|None;journal_attempt_count:int|None;journal_success_count:int|None;journal_failure_count:int|None
    def __post_init__(self):
        for n in self.__dataclass_fields__:
            v=getattr(self,n)
            if v is not None and (isinstance(v,bool) or not isinstance(v,int) or v<0):raise BacktestReportValidationError("activity counts must be non-negative integers or None")
    def to_dict(self):return {n:getattr(self,n) for n in self.__dataclass_fields__}
@dataclass(frozen=True,slots=True)
class BacktestReportPerformanceSummary:
    analytics_result:AnalyticsResult|None
    def __post_init__(self):
        if self.analytics_result is not None and not isinstance(self.analytics_result,AnalyticsResult):raise BacktestReportValidationError("analytics_result is invalid")
    def to_dict(self):return {"analytics_result":self.analytics_result.to_dict() if self.analytics_result else None}
@dataclass(frozen=True,slots=True)
class BacktestReportIssueSummary:
    warnings:tuple[str,...];errors:tuple[str,...];terminal_error_type:str|None=None
    def __post_init__(self):object.__setattr__(self,"warnings",_strings(self.warnings,"warnings"));object.__setattr__(self,"errors",_strings(self.errors,"errors"));object.__setattr__(self,"terminal_error_type",_text(self.terminal_error_type,"terminal_error_type",True))
    def to_dict(self):return {"warnings":list(self.warnings),"errors":list(self.errors),"terminal_error_type":self.terminal_error_type}
@dataclass(frozen=True,slots=True)
class BacktestReport:
    identity:BacktestReportIdentity;status:BacktestReportStatus;overview:BacktestReportOverview;activity:BacktestReportActivitySummary;performance:BacktestReportPerformanceSummary;stages:tuple[BacktestReportStageSummary,...];issues:BacktestReportIssueSummary;source_run_result:BacktestRunResult
    def __post_init__(self):
        if not isinstance(self.identity,BacktestReportIdentity) or not isinstance(self.status,BacktestReportStatus) or not isinstance(self.overview,BacktestReportOverview) or not isinstance(self.activity,BacktestReportActivitySummary) or not isinstance(self.performance,BacktestReportPerformanceSummary) or not isinstance(self.issues,BacktestReportIssueSummary) or not isinstance(self.source_run_result,BacktestRunResult):raise BacktestReportValidationError("report contracts are invalid")
        if not isinstance(self.stages,tuple) or any(not isinstance(x,BacktestReportStageSummary) for x in self.stages):raise BacktestReportValidationError("stages must be immutable")
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"overview":self.overview.to_dict(),"activity":self.activity.to_dict(),"performance":self.performance.to_dict(),"stages":[x.to_dict() for x in self.stages],"issues":self.issues.to_dict(),"source_run_result":self.source_run_result.to_dict()}
@dataclass(frozen=True,slots=True)
class BacktestReportResult:
    identity:BacktestReportIdentity;status:BacktestReportStatus;report:BacktestReport|None;requested_at:datetime;criteria_results:tuple[BacktestReportCriteriaResult,...];errors:tuple[str,...]=();error_type:str|None=None
    def __post_init__(self):
        if not isinstance(self.identity,BacktestReportIdentity) or not isinstance(self.status,BacktestReportStatus):raise BacktestReportValidationError("report result identity or status is invalid")
        if self.report is not None and not isinstance(self.report,BacktestReport):raise BacktestReportValidationError("report is invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"))
        if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,BacktestReportCriteriaResult) for x in self.criteria_results):raise BacktestReportValidationError("criteria_results must be immutable")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"));object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True))
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"report":self.report.to_dict() if self.report else None,"requested_at":self.requested_at.isoformat(),"criteria_results":[x.to_dict() for x in self.criteria_results],"errors":list(self.errors),"error_type":self.error_type}
