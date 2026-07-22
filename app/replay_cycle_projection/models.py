"""Immutable records for projecting replay outcomes through Trading Cycle."""
from dataclasses import dataclass,field
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.historical_replay import HistoricalReplayResult
from app.trading_cycle import TradingCycle
from app.replay_cycle_projection.exceptions import ReplayCycleProjectionValidationError

def _text(v,n,optional=False):
    if optional and v is None:return None
    if not isinstance(v,str) or not v.strip() or v!=v.strip():raise ReplayCycleProjectionValidationError(f"{n} must be a non-empty stripped string")
    return v
def _strings(v,n):
    if not isinstance(v,tuple) or any(not isinstance(x,str) or not x.strip() for x in v):raise ReplayCycleProjectionValidationError(f"{n} must be immutable strings")
    return v
class ReplayCycleProjectionStatus(StrEnum):
    COMPLETED="COMPLETED";PARTIALLY_COMPLETED="PARTIALLY_COMPLETED";EMPTY="EMPTY";DISABLED="DISABLED";REJECTED="REJECTED";FAILED="FAILED"
class ReplayCycleProjectionItemStatus(StrEnum):
    COMPLETED="COMPLETED";INELIGIBLE="INELIGIBLE";SKIPPED="SKIPPED";REJECTED="REJECTED";FAILED="FAILED"
class ReplayCycleProjectionFailureMode(StrEnum):STOP_ON_FAILURE="STOP_ON_FAILURE";CONTINUE_ON_FAILURE="CONTINUE_ON_FAILURE"
@dataclass(frozen=True,slots=True)
class ReplayCycleProjectionRequest:
    replay_result:HistoricalReplayResult;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.replay_result,HistoricalReplayResult):raise ReplayCycleProjectionValidationError("replay_result must be HistoricalReplayResult")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"replay_result":self.replay_result.to_dict(),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(HistoricalReplayResult.from_dict(v["replay_result"]),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class ReplayCycleProjectionItemResult:
    replay_id:str;event_id:str;sequence:int;cycle_id:str;status:ReplayCycleProjectionItemStatus;cycle:TradingCycle|None=None;reasons:tuple[str,...]=();errors:tuple[str,...]=();failed_stage:str|None=None;exception_type:str|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        for n in ("replay_id","event_id","cycle_id"):object.__setattr__(self,n,_text(getattr(self,n),n))
        if isinstance(self.sequence,bool) or not isinstance(self.sequence,int) or self.sequence<0:raise ReplayCycleProjectionValidationError("sequence must be non-negative integer")
        if not isinstance(self.status,ReplayCycleProjectionItemStatus):raise ReplayCycleProjectionValidationError("item status is invalid")
        if self.cycle is not None and not isinstance(self.cycle,TradingCycle):raise ReplayCycleProjectionValidationError("cycle must be TradingCycle")
        if self.status in (ReplayCycleProjectionItemStatus.COMPLETED,ReplayCycleProjectionItemStatus.REJECTED) and self.cycle is None:raise ReplayCycleProjectionValidationError("successful projection item requires cycle")
        if self.cycle is not None and self.cycle.identity.cycle_id!=self.cycle_id:raise ReplayCycleProjectionValidationError("item cycle identity mismatch")
        for n in ("reasons","errors"):object.__setattr__(self,n,_strings(getattr(self,n),n))
        for n in ("failed_stage","exception_type"):object.__setattr__(self,n,_text(getattr(self,n),n,True))
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"replay_id":self.replay_id,"event_id":self.event_id,"sequence":self.sequence,"cycle_id":self.cycle_id,"status":self.status.value,"cycle":self.cycle.to_dict() if self.cycle else None,"reasons":list(self.reasons),"errors":list(self.errors),"failed_stage":self.failed_stage,"exception_type":self.exception_type,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(v["replay_id"],v["event_id"],v["sequence"],v["cycle_id"],ReplayCycleProjectionItemStatus(v["status"]),TradingCycle.from_dict(v["cycle"]) if v.get("cycle") else None,tuple(v.get("reasons",())),tuple(v.get("errors",())),v.get("failed_stage"),v.get("exception_type"),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class ReplayCycleProjectionProgress:
    total_items:int;eligible_items:int;completed_items:int;rejected_items:int;ineligible_items:int;failed_items:int;skipped_items:int
    def __post_init__(self):
        for n in self.__dataclass_fields__:
            if isinstance(getattr(self,n),bool) or not isinstance(getattr(self,n),int) or getattr(self,n)<0:raise ReplayCycleProjectionValidationError("progress counts must be non-negative integers")
        if self.completed_items+self.rejected_items+self.ineligible_items+self.failed_items+self.skipped_items!=self.total_items:raise ReplayCycleProjectionValidationError("progress counts are inconsistent")
        if self.completed_items+self.rejected_items+self.failed_items+self.skipped_items!=self.eligible_items:raise ReplayCycleProjectionValidationError("eligible progress count is inconsistent")
    def to_dict(self):return {n:getattr(self,n) for n in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls,v):return cls(**dict(v))
@dataclass(frozen=True,slots=True)
class ReplayCycleProjectionCriteriaResult:
    criterion:str;passed:bool;reasons:tuple[str,...]=()
    def __post_init__(self):
        object.__setattr__(self,"criterion",_text(self.criterion,"criterion"))
        if not isinstance(self.passed,bool):raise ReplayCycleProjectionValidationError("passed must be boolean")
        object.__setattr__(self,"reasons",_strings(self.reasons,"reasons"))
    def to_dict(self):return {"criterion":self.criterion,"passed":self.passed,"reasons":list(self.reasons)}
    @classmethod
    def from_dict(cls,v):return cls(v["criterion"],v["passed"],tuple(v.get("reasons",())))
@dataclass(frozen=True,slots=True)
class ReplayCycleProjectionResult:
    replay_result:HistoricalReplayResult;status:ReplayCycleProjectionStatus;item_results:tuple[ReplayCycleProjectionItemResult,...];cycles:tuple[TradingCycle,...];progress:ReplayCycleProjectionProgress;criteria_results:tuple[ReplayCycleProjectionCriteriaResult,...];warnings:tuple[str,...]=();errors:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict);disabled:bool=False
    def __post_init__(self):
        if not isinstance(self.replay_result,HistoricalReplayResult) or not isinstance(self.status,ReplayCycleProjectionStatus) or not isinstance(self.progress,ReplayCycleProjectionProgress):raise ReplayCycleProjectionValidationError("projection result core is invalid")
        if not isinstance(self.item_results,tuple) or any(not isinstance(x,ReplayCycleProjectionItemResult) for x in self.item_results):raise ReplayCycleProjectionValidationError("item_results must be immutable")
        if not isinstance(self.cycles,tuple) or any(not isinstance(x,TradingCycle) for x in self.cycles):raise ReplayCycleProjectionValidationError("cycles must be immutable")
        if len(self.item_results)!=self.progress.total_items:raise ReplayCycleProjectionValidationError("item count mismatch")
        counts={s:sum(x.status is s for x in self.item_results) for s in ReplayCycleProjectionItemStatus}
        actual=(counts[ReplayCycleProjectionItemStatus.COMPLETED],counts[ReplayCycleProjectionItemStatus.REJECTED],counts[ReplayCycleProjectionItemStatus.INELIGIBLE],counts[ReplayCycleProjectionItemStatus.FAILED],counts[ReplayCycleProjectionItemStatus.SKIPPED])
        expected=(self.progress.completed_items,self.progress.rejected_items,self.progress.ineligible_items,self.progress.failed_items,self.progress.skipped_items)
        if actual!=expected:raise ReplayCycleProjectionValidationError("progress does not match item results")
        projected=tuple(x.cycle for x in self.item_results if x.cycle is not None)
        if self.cycles!=projected:raise ReplayCycleProjectionValidationError("cycle order must match projected items")
        if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,ReplayCycleProjectionCriteriaResult) for x in self.criteria_results):raise ReplayCycleProjectionValidationError("criteria_results must be immutable")
        for n in ("warnings","errors"):object.__setattr__(self,n,_strings(getattr(self,n),n))
        if not isinstance(self.disabled,bool) or (self.status is ReplayCycleProjectionStatus.DISABLED)!=self.disabled:raise ReplayCycleProjectionValidationError("disabled status is inconsistent")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"replay_result":self.replay_result.to_dict(),"status":self.status.value,"item_results":[x.to_dict() for x in self.item_results],"cycles":[x.to_dict() for x in self.cycles],"progress":self.progress.to_dict(),"criteria_results":[x.to_dict() for x in self.criteria_results],"warnings":list(self.warnings),"errors":list(self.errors),"metadata":thaw_json_value(self.metadata),"disabled":self.disabled}
    @classmethod
    def from_dict(cls,v):return cls(HistoricalReplayResult.from_dict(v["replay_result"]),ReplayCycleProjectionStatus(v["status"]),tuple(ReplayCycleProjectionItemResult.from_dict(x) for x in v["item_results"]),tuple(TradingCycle.from_dict(x) for x in v["cycles"]),ReplayCycleProjectionProgress.from_dict(v["progress"]),tuple(ReplayCycleProjectionCriteriaResult.from_dict(x) for x in v["criteria_results"]),tuple(v.get("warnings",())),tuple(v.get("errors",())),v.get("metadata",{}),v.get("disabled",False))
