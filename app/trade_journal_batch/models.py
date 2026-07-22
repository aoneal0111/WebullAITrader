"""Immutable application records for deterministic ordered journal appends."""
from dataclasses import dataclass,field
from datetime import datetime
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.trade_journal import TradeJournalAppendResult,TradeJournalPolicy,TradeJournalState
from app.trading_cycle import TradingCycle
from app.trade_journal_batch.exceptions import TradeJournalBatchValidationError
def _text(v,n,optional=False):
    if optional and v is None:return None
    if not isinstance(v,str) or not v.strip() or v!=v.strip():raise TradeJournalBatchValidationError(f"{n} must be a non-empty stripped string")
    return v
def _time(v,n):
    if not isinstance(v,datetime) or v.tzinfo is None or v.utcoffset() is None:raise TradeJournalBatchValidationError(f"{n} must be timezone-aware")
    return v
def _strings(v,n):
    if not isinstance(v,tuple) or any(not isinstance(x,str) or not x.strip() for x in v):raise TradeJournalBatchValidationError(f"{n} must be immutable strings")
    return v
class TradeJournalBatchStatus(StrEnum):COMPLETED="COMPLETED";PARTIALLY_COMPLETED="PARTIALLY_COMPLETED";EMPTY="EMPTY";DISABLED="DISABLED";REJECTED="REJECTED";FAILED="FAILED"
class TradeJournalBatchItemStatus(StrEnum):COMPLETED="COMPLETED";REJECTED="REJECTED";SKIPPED="SKIPPED";FAILED="FAILED"
class TradeJournalBatchFailureMode(StrEnum):STOP_ON_FAILURE="STOP_ON_FAILURE";CONTINUE_ON_FAILURE="CONTINUE_ON_FAILURE"
@dataclass(frozen=True,slots=True)
class TradeJournalBatchIdentity:
    batch_id:str;journal_id:str;source_run_id:str|None=None
    def __post_init__(self):
        for n in ("batch_id","journal_id"):object.__setattr__(self,n,_text(getattr(self,n),n))
        object.__setattr__(self,"source_run_id",_text(self.source_run_id,"source_run_id",True))
    def to_dict(self):return {n:getattr(self,n) for n in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls,v):return cls(**dict(v))
@dataclass(frozen=True,slots=True)
class TradeJournalBatchItem:
    entry_id:str;cycle:TradingCycle;recorded_at:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"entry_id",_text(self.entry_id,"entry_id"))
        if not isinstance(self.cycle,TradingCycle):raise TradeJournalBatchValidationError("cycle must be TradingCycle")
        object.__setattr__(self,"recorded_at",_time(self.recorded_at,"recorded_at"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"entry_id":self.entry_id,"cycle":self.cycle.to_dict(),"recorded_at":self.recorded_at.isoformat(),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(v["entry_id"],TradingCycle.from_dict(v["cycle"]),datetime.fromisoformat(v["recorded_at"]),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class TradeJournalBatchPolicy:
    enabled:bool=True;allow_empty:bool=False;failure_mode:TradeJournalBatchFailureMode=TradeJournalBatchFailureMode.STOP_ON_FAILURE;include_diagnostics:bool=True
    def __post_init__(self):
        if any(not isinstance(getattr(self,n),bool) for n in ("enabled","allow_empty","include_diagnostics")):raise TradeJournalBatchValidationError("policy flags must be boolean")
        if not isinstance(self.failure_mode,TradeJournalBatchFailureMode):raise TradeJournalBatchValidationError("failure_mode is invalid")
    def to_dict(self):return {"enabled":self.enabled,"allow_empty":self.allow_empty,"failure_mode":self.failure_mode.value,"include_diagnostics":self.include_diagnostics}
    @classmethod
    def from_dict(cls,v):return cls(v.get("enabled",True),v.get("allow_empty",False),TradeJournalBatchFailureMode(v.get("failure_mode","STOP_ON_FAILURE")),v.get("include_diagnostics",True))
@dataclass(frozen=True,slots=True)
class TradeJournalBatchRequest:
    identity:TradeJournalBatchIdentity;initial_journal:TradeJournalState;items:tuple[TradeJournalBatchItem,...];journal_policy:TradeJournalPolicy;policy:TradeJournalBatchPolicy;requested_at:datetime;completed_at:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.identity,TradeJournalBatchIdentity) or not isinstance(self.initial_journal,TradeJournalState):raise TradeJournalBatchValidationError("identity and initial_journal are required")
        if not isinstance(self.items,tuple) or any(not isinstance(x,TradeJournalBatchItem) for x in self.items):raise TradeJournalBatchValidationError("items must be immutable TradeJournalBatchItems")
        if not isinstance(self.journal_policy,TradeJournalPolicy) or not isinstance(self.policy,TradeJournalBatchPolicy):raise TradeJournalBatchValidationError("journal_policy and policy are required")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if self.completed_at<self.requested_at:raise TradeJournalBatchValidationError("completed_at cannot precede requested_at")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"identity":self.identity.to_dict(),"initial_journal":self.initial_journal.to_dict(),"items":[x.to_dict() for x in self.items],"journal_policy":self.journal_policy.to_dict(),"policy":self.policy.to_dict(),"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(TradeJournalBatchIdentity.from_dict(v["identity"]),TradeJournalState.from_dict(v["initial_journal"]),tuple(TradeJournalBatchItem.from_dict(x) for x in v["items"]),TradeJournalPolicy.from_dict(v["journal_policy"]),TradeJournalBatchPolicy.from_dict(v["policy"]),datetime.fromisoformat(v["requested_at"]),datetime.fromisoformat(v["completed_at"]),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class TradeJournalBatchCriteriaResult:
    criterion:str;passed:bool;reasons:tuple[str,...]=()
    def __post_init__(self):
        object.__setattr__(self,"criterion",_text(self.criterion,"criterion"))
        if not isinstance(self.passed,bool):raise TradeJournalBatchValidationError("passed must be boolean")
        object.__setattr__(self,"reasons",_strings(self.reasons,"reasons"))
    def to_dict(self):return {"criterion":self.criterion,"passed":self.passed,"reasons":list(self.reasons)}
    @classmethod
    def from_dict(cls,v):return cls(v["criterion"],v["passed"],tuple(v.get("reasons",())))
@dataclass(frozen=True,slots=True)
class TradeJournalBatchItemResult:
    index:int;cycle_id:str;entry_id:str;status:TradeJournalBatchItemStatus;append_result:TradeJournalAppendResult|None=None;message:str|None=None;error_type:str|None=None
    def __post_init__(self):
        if isinstance(self.index,bool) or not isinstance(self.index,int) or self.index<0:raise TradeJournalBatchValidationError("index must be non-negative integer")
        for n in ("cycle_id","entry_id"):object.__setattr__(self,n,_text(getattr(self,n),n))
        if not isinstance(self.status,TradeJournalBatchItemStatus):raise TradeJournalBatchValidationError("item status is invalid")
        if self.append_result is not None and not isinstance(self.append_result,TradeJournalAppendResult):raise TradeJournalBatchValidationError("append_result is invalid")
        object.__setattr__(self,"message",_text(self.message,"message",True));object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True))
    def to_dict(self):return {"index":self.index,"cycle_id":self.cycle_id,"entry_id":self.entry_id,"status":self.status.value,"append_result":self.append_result.to_dict() if self.append_result else None,"message":self.message,"error_type":self.error_type}
    @classmethod
    def from_dict(cls,v):return cls(v["index"],v["cycle_id"],v["entry_id"],TradeJournalBatchItemStatus(v["status"]),TradeJournalAppendResult.from_dict(v["append_result"]) if v.get("append_result") else None,v.get("message"),v.get("error_type"))
@dataclass(frozen=True,slots=True)
class TradeJournalBatchProgress:
    total_count:int;completed_count:int;rejected_count:int;failed_count:int;skipped_count:int
    def __post_init__(self):
        for n in self.__dataclass_fields__:
            if isinstance(getattr(self,n),bool) or not isinstance(getattr(self,n),int) or getattr(self,n)<0:raise TradeJournalBatchValidationError("progress counts must be non-negative integers")
        if self.completed_count+self.rejected_count+self.failed_count+self.skipped_count>self.total_count:raise TradeJournalBatchValidationError("progress counts are inconsistent")
    def to_dict(self):return {n:getattr(self,n) for n in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls,v):return cls(**dict(v))
@dataclass(frozen=True,slots=True)
class TradeJournalBatchResult:
    identity:TradeJournalBatchIdentity;status:TradeJournalBatchStatus;initial_journal:TradeJournalState;final_journal:TradeJournalState;item_results:tuple[TradeJournalBatchItemResult,...];progress:TradeJournalBatchProgress;requested_at:datetime;completed_at:datetime;criteria_results:tuple[TradeJournalBatchCriteriaResult,...];warnings:tuple[str,...]=();errors:tuple[str,...]=();disabled:bool=False
    def __post_init__(self):
        if not isinstance(self.identity,TradeJournalBatchIdentity) or not isinstance(self.status,TradeJournalBatchStatus):raise TradeJournalBatchValidationError("result identity or status is invalid")
        if not isinstance(self.initial_journal,TradeJournalState) or not isinstance(self.final_journal,TradeJournalState) or not isinstance(self.progress,TradeJournalBatchProgress):raise TradeJournalBatchValidationError("result journal or progress is invalid")
        if not isinstance(self.item_results,tuple) or any(not isinstance(x,TradeJournalBatchItemResult) for x in self.item_results):raise TradeJournalBatchValidationError("item_results must be immutable")
        if self.item_results and tuple(x.index for x in self.item_results)!=tuple(range(len(self.item_results))):raise TradeJournalBatchValidationError("item result order is invalid")
        if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,TradeJournalBatchCriteriaResult) for x in self.criteria_results):raise TradeJournalBatchValidationError("criteria_results must be immutable")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        for n in ("warnings","errors"):object.__setattr__(self,n,_strings(getattr(self,n),n))
        if not isinstance(self.disabled,bool) or (self.status is TradeJournalBatchStatus.DISABLED)!=self.disabled:raise TradeJournalBatchValidationError("disabled status is inconsistent")
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"initial_journal":self.initial_journal.to_dict(),"final_journal":self.final_journal.to_dict(),"item_results":[x.to_dict() for x in self.item_results],"progress":self.progress.to_dict(),"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"criteria_results":[x.to_dict() for x in self.criteria_results],"warnings":list(self.warnings),"errors":list(self.errors),"disabled":self.disabled}
    @classmethod
    def from_dict(cls,v):return cls(TradeJournalBatchIdentity.from_dict(v["identity"]),TradeJournalBatchStatus(v["status"]),TradeJournalState.from_dict(v["initial_journal"]),TradeJournalState.from_dict(v["final_journal"]),tuple(TradeJournalBatchItemResult.from_dict(x) for x in v["item_results"]),TradeJournalBatchProgress.from_dict(v["progress"]),datetime.fromisoformat(v["requested_at"]),datetime.fromisoformat(v["completed_at"]),tuple(TradeJournalBatchCriteriaResult.from_dict(x) for x in v["criteria_results"]),tuple(v.get("warnings",())),tuple(v.get("errors",())),v.get("disabled",False))
