"""Immutable application coordination records; no domain calculations live here."""
from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal,InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.historical_replay import HistoricalReplayRequest,HistoricalReplayResult
from app.replay_cycle_projection import ReplayCycleProjectionResult
from app.trade_journal import TradeJournalPolicy,TradeJournalState
from app.trade_journal_batch import TradeJournalBatchIdentity,TradeJournalBatchPolicy,TradeJournalBatchResult
from app.analytics import AnalyticsResult
from app.backtest_run.exceptions import BacktestRunValidationError
def _text(v,n,optional=False):
    if optional and v is None:return None
    if not isinstance(v,str) or not v.strip() or v!=v.strip():raise BacktestRunValidationError(f"{n} must be a non-empty stripped string")
    return v
def _time(v,n):
    if not isinstance(v,datetime) or v.tzinfo is None or v.utcoffset() is None:raise BacktestRunValidationError(f"{n} must be timezone-aware")
    return v
def _decimal(v,n):
    if v is None:return None
    if isinstance(v,bool) or not isinstance(v,(Decimal,str,int)):raise BacktestRunValidationError(f"{n} must be Decimal-compatible")
    try:r=Decimal(v)
    except (InvalidOperation,ValueError) as exc:raise BacktestRunValidationError(f"{n} must be finite") from exc
    if not r.is_finite() or r<0:raise BacktestRunValidationError(f"{n} must be finite and non-negative")
    return r
def _strings(v,n):
    if not isinstance(v,tuple) or any(not isinstance(x,str) or not x.strip() for x in v):raise BacktestRunValidationError(f"{n} must be immutable strings")
    return v
class BacktestRunStatus(StrEnum):COMPLETED="COMPLETED";PARTIALLY_COMPLETED="PARTIALLY_COMPLETED";EMPTY="EMPTY";DISABLED="DISABLED";REJECTED="REJECTED";FAILED="FAILED"
class BacktestRunStage(StrEnum):VALIDATION="VALIDATION";HISTORICAL_REPLAY="HISTORICAL_REPLAY";CYCLE_PROJECTION="CYCLE_PROJECTION";TRADE_JOURNAL_BATCH="TRADE_JOURNAL_BATCH";ANALYTICS="ANALYTICS";COMPLETED="COMPLETED"
class BacktestRunStageStatus(StrEnum):COMPLETED="COMPLETED";REJECTED="REJECTED";SKIPPED="SKIPPED";FAILED="FAILED"
@dataclass(frozen=True,slots=True)
class BacktestRunIdentity:
    run_id:str;source_id:str|None=None
    def __post_init__(self):object.__setattr__(self,"run_id",_text(self.run_id,"run_id"));object.__setattr__(self,"source_id",_text(self.source_id,"source_id",True))
    def to_dict(self):return {"run_id":self.run_id,"source_id":self.source_id}
    @classmethod
    def from_dict(cls,v):return cls(v["run_id"],v.get("source_id"))
@dataclass(frozen=True,slots=True)
class BacktestRunPolicy:
    enabled:bool=True;allow_empty:bool=False
    def __post_init__(self):
        if not isinstance(self.enabled,bool) or not isinstance(self.allow_empty,bool):raise BacktestRunValidationError("policy flags must be boolean")
    def to_dict(self):return {"enabled":self.enabled,"allow_empty":self.allow_empty}
    @classmethod
    def from_dict(cls,v):return cls(v.get("enabled",True),v.get("allow_empty",False))
@dataclass(frozen=True,slots=True)
class BacktestJournalItemInput:
    cycle_id:str;entry_id:str;recorded_at:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):object.__setattr__(self,"cycle_id",_text(self.cycle_id,"cycle_id"));object.__setattr__(self,"entry_id",_text(self.entry_id,"entry_id"));object.__setattr__(self,"recorded_at",_time(self.recorded_at,"recorded_at"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"cycle_id":self.cycle_id,"entry_id":self.entry_id,"recorded_at":self.recorded_at.isoformat(),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(v["cycle_id"],v["entry_id"],datetime.fromisoformat(v["recorded_at"]),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class BacktestJournalInput:
    identity:TradeJournalBatchIdentity;initial_journal:TradeJournalState;items:tuple[BacktestJournalItemInput,...];journal_policy:TradeJournalPolicy;batch_policy:TradeJournalBatchPolicy;requested_at:datetime;completed_at:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.identity,TradeJournalBatchIdentity) or not isinstance(self.initial_journal,TradeJournalState) or not isinstance(self.journal_policy,TradeJournalPolicy) or not isinstance(self.batch_policy,TradeJournalBatchPolicy):raise BacktestRunValidationError("journal input contracts are invalid")
        if not isinstance(self.items,tuple) or any(not isinstance(x,BacktestJournalItemInput) for x in self.items):raise BacktestRunValidationError("journal item inputs must be immutable")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"journal requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"journal completed_at"))
        if self.completed_at<self.requested_at:raise BacktestRunValidationError("journal completed_at cannot precede requested_at")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"identity":self.identity.to_dict(),"initial_journal":self.initial_journal.to_dict(),"items":[x.to_dict() for x in self.items],"journal_policy":self.journal_policy.to_dict(),"batch_policy":self.batch_policy.to_dict(),"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"metadata":thaw_json_value(self.metadata)}
@dataclass(frozen=True,slots=True)
class BacktestAnalyticsInput:
    request_id:str;as_of:datetime;starting_equity:Decimal|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):object.__setattr__(self,"request_id",_text(self.request_id,"analytics request_id"));object.__setattr__(self,"as_of",_time(self.as_of,"analytics as_of"));object.__setattr__(self,"starting_equity",_decimal(self.starting_equity,"starting_equity"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"request_id":self.request_id,"as_of":self.as_of.isoformat(),"starting_equity":str(self.starting_equity) if self.starting_equity is not None else None,"metadata":thaw_json_value(self.metadata)}
@dataclass(frozen=True,slots=True)
class BacktestRunRequest:
    identity:BacktestRunIdentity;replay_request:HistoricalReplayRequest;projection_metadata:Mapping[str,JSONValue];journal_input:BacktestJournalInput;analytics_input:BacktestAnalyticsInput;policy:BacktestRunPolicy;requested_at:datetime;completed_at:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.identity,BacktestRunIdentity) or not isinstance(self.replay_request,HistoricalReplayRequest) or not isinstance(self.journal_input,BacktestJournalInput) or not isinstance(self.analytics_input,BacktestAnalyticsInput) or not isinstance(self.policy,BacktestRunPolicy):raise BacktestRunValidationError("backtest request contracts are invalid")
        object.__setattr__(self,"projection_metadata",freeze_json_mapping("projection_metadata",self.projection_metadata));object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if self.completed_at<self.requested_at:raise BacktestRunValidationError("completed_at cannot precede requested_at")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"identity":self.identity.to_dict(),"replay_request":self.replay_request.to_dict(),"projection_metadata":thaw_json_value(self.projection_metadata),"journal_input":self.journal_input.to_dict(),"analytics_input":self.analytics_input.to_dict(),"policy":self.policy.to_dict(),"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"metadata":thaw_json_value(self.metadata)}
@dataclass(frozen=True,slots=True)
class BacktestRunCriteriaResult:
    criterion:str;passed:bool;reasons:tuple[str,...]=()
    def __post_init__(self):object.__setattr__(self,"criterion",_text(self.criterion,"criterion"));isinstance(self.passed,bool) or (_ for _ in ()).throw(BacktestRunValidationError("passed must be boolean"));object.__setattr__(self,"reasons",_strings(self.reasons,"reasons"))
    def to_dict(self):return {"criterion":self.criterion,"passed":self.passed,"reasons":list(self.reasons)}
@dataclass(frozen=True,slots=True)
class BacktestRunStageResult:
    stage:BacktestRunStage;status:BacktestRunStageStatus;message:str|None=None;error_type:str|None=None
    def __post_init__(self):
        if not isinstance(self.stage,BacktestRunStage) or not isinstance(self.status,BacktestRunStageStatus):raise BacktestRunValidationError("stage result enums are invalid")
        object.__setattr__(self,"message",_text(self.message,"message",True));object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True))
    def to_dict(self):return {"stage":self.stage.value,"status":self.status.value,"message":self.message,"error_type":self.error_type}
    @classmethod
    def from_dict(cls,v):return cls(BacktestRunStage(v["stage"]),BacktestRunStageStatus(v["status"]),v.get("message"),v.get("error_type"))
@dataclass(frozen=True,slots=True)
class BacktestRunResult:
    identity:BacktestRunIdentity;status:BacktestRunStatus;stopped_at:BacktestRunStage;replay_result:HistoricalReplayResult|None;projection_result:ReplayCycleProjectionResult|None;journal_batch_result:TradeJournalBatchResult|None;analytics_result:AnalyticsResult|None;stage_results:tuple[BacktestRunStageResult,...];requested_at:datetime;completed_at:datetime;criteria_results:tuple[BacktestRunCriteriaResult,...];warnings:tuple[str,...]=();errors:tuple[str,...]=();error_type:str|None=None
    def __post_init__(self):
        if not isinstance(self.identity,BacktestRunIdentity) or not isinstance(self.status,BacktestRunStatus) or not isinstance(self.stopped_at,BacktestRunStage):raise BacktestRunValidationError("result identity or status is invalid")
        expected=((self.replay_result,HistoricalReplayResult),(self.projection_result,ReplayCycleProjectionResult),(self.journal_batch_result,TradeJournalBatchResult),(self.analytics_result,AnalyticsResult))
        if any(v is not None and not isinstance(v,t) for v,t in expected):raise BacktestRunValidationError("downstream result type is invalid")
        if not isinstance(self.stage_results,tuple) or tuple(x.stage for x in self.stage_results)!=tuple(BacktestRunStage):raise BacktestRunValidationError("stage results must contain every stage in order")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,BacktestRunCriteriaResult) for x in self.criteria_results):raise BacktestRunValidationError("criteria results must be immutable")
        for n in ("warnings","errors"):object.__setattr__(self,n,_strings(getattr(self,n),n))
        object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True))
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"stopped_at":self.stopped_at.value,"replay_result":self.replay_result.to_dict() if self.replay_result else None,"projection_result":self.projection_result.to_dict() if self.projection_result else None,"journal_batch_result":self.journal_batch_result.to_dict() if self.journal_batch_result else None,"analytics_result":self.analytics_result.to_dict() if self.analytics_result else None,"stage_results":[x.to_dict() for x in self.stage_results],"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"criteria_results":[x.to_dict() for x in self.criteria_results],"warnings":list(self.warnings),"errors":list(self.errors),"error_type":self.error_type}
