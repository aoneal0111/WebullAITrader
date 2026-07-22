from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal,InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.execution_orchestrator import PaperTradingCycleResult
from app.paper_trading import PaperTradingAccount
from app.portfolio import PortfolioSnapshot
from app.historical_replay.exceptions import HistoricalReplayValidationError
def _text(v,n,optional=False):
    if optional and v is None:return None
    if not isinstance(v,str) or not v.strip() or v!=v.strip():raise HistoricalReplayValidationError(f"{n} must be a non-empty stripped string")
    return v
def _decimal(v,n,positive=False):
    if v is None:return None
    if isinstance(v,bool) or not isinstance(v,(Decimal,str,int)):raise HistoricalReplayValidationError(f"{n} must be Decimal-compatible")
    try:r=Decimal(v)
    except (InvalidOperation,ValueError) as exc:raise HistoricalReplayValidationError(f"{n} must be finite") from exc
    if not r.is_finite() or (positive and r<=0) or (not positive and r<0):raise HistoricalReplayValidationError(f"{n} is invalid")
    return r
def _time(v,n,optional=False):
    if optional and v is None:return None
    if not isinstance(v,datetime) or v.tzinfo is None or v.utcoffset() is None:raise HistoricalReplayValidationError(f"{n} must be timezone-aware")
    return v
def _strings(v,n):
    if not isinstance(v,tuple) or any(not isinstance(x,str) or not x.strip() for x in v):raise HistoricalReplayValidationError(f"{n} must be immutable strings")
    return v
class HistoricalReplayStatus(StrEnum):COMPLETED="COMPLETED";PARTIALLY_COMPLETED="PARTIALLY_COMPLETED";DISABLED="DISABLED";EMPTY="EMPTY";FAILED="FAILED"
class HistoricalReplayEventStatus(StrEnum):COMPLETED="COMPLETED";REJECTED="REJECTED";SKIPPED="SKIPPED";FAILED="FAILED"
class HistoricalReplayOrdering(StrEnum):INPUT_ORDER="INPUT_ORDER";EVENT_TIME="EVENT_TIME";EVENT_TIME_THEN_SEQUENCE="EVENT_TIME_THEN_SEQUENCE"
class HistoricalReplayFailureMode(StrEnum):STOP_ON_FAILURE="STOP_ON_FAILURE";CONTINUE_ON_FAILURE="CONTINUE_ON_FAILURE"
@dataclass(frozen=True,slots=True)
class HistoricalReplayIdentity:
    replay_id:str;request_id:str;account_id:str;dataset_id:str|None=None;run_id:str|None=None
    def __post_init__(self):
        for n in ("replay_id","request_id","account_id"):object.__setattr__(self,n,_text(getattr(self,n),n))
        for n in ("dataset_id","run_id"):object.__setattr__(self,n,_text(getattr(self,n),n,True))
    def to_dict(self):return {n:getattr(self,n) for n in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls,v):return cls(**dict(v))
@dataclass(frozen=True,slots=True)
class HistoricalReplayEvent:
    event_id:str;orchestrator_request_id:str;sequence:int;symbol:str;event_time:datetime;portfolio:PortfolioSnapshot;market_price:Decimal;received_time:datetime|None=None;bid_price:Decimal|None=None;ask_price:Decimal|None=None;available_quantity:Decimal|None=None;requested_quantity:Decimal|None=None;features:Mapping[str,JSONValue]=field(default_factory=dict);metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"event_id",_text(self.event_id,"event_id"));object.__setattr__(self,"orchestrator_request_id",_text(self.orchestrator_request_id,"orchestrator_request_id"))
        if isinstance(self.sequence,bool) or not isinstance(self.sequence,int) or self.sequence<0:raise HistoricalReplayValidationError("sequence must be non-negative integer")
        object.__setattr__(self,"symbol",_text(self.symbol,"symbol").upper());object.__setattr__(self,"event_time",_time(self.event_time,"event_time"));object.__setattr__(self,"received_time",_time(self.received_time,"received_time",True))
        if not isinstance(self.portfolio,PortfolioSnapshot):raise HistoricalReplayValidationError("portfolio must be PortfolioSnapshot")
        for n in ("market_price","bid_price","ask_price"):object.__setattr__(self,n,_decimal(getattr(self,n),n,True))
        for n in ("available_quantity","requested_quantity"):object.__setattr__(self,n,_decimal(getattr(self,n),n))
        object.__setattr__(self,"features",freeze_json_mapping("features",self.features));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"event_id":self.event_id,"orchestrator_request_id":self.orchestrator_request_id,"sequence":self.sequence,"symbol":self.symbol,"event_time":self.event_time.isoformat(),"portfolio":self.portfolio.to_dict(),"market_price":str(self.market_price),"received_time":self.received_time.isoformat() if self.received_time else None,"bid_price":str(self.bid_price) if self.bid_price is not None else None,"ask_price":str(self.ask_price) if self.ask_price is not None else None,"available_quantity":str(self.available_quantity) if self.available_quantity is not None else None,"requested_quantity":str(self.requested_quantity) if self.requested_quantity is not None else None,"features":thaw_json_value(self.features),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):
        d=dict(v);d["event_time"]=datetime.fromisoformat(d["event_time"]);d["received_time"]=datetime.fromisoformat(d["received_time"]) if d.get("received_time") else None;d["portfolio"]=PortfolioSnapshot.from_dict(d["portfolio"]);return cls(**d)
@dataclass(frozen=True,slots=True)
class HistoricalReplayRequest:
    identity:HistoricalReplayIdentity;events:tuple[HistoricalReplayEvent,...];started_at:datetime;initial_paper_account:PaperTradingAccount;completed_at:datetime|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.identity,HistoricalReplayIdentity):raise HistoricalReplayValidationError("identity is required")
        if not isinstance(self.events,tuple) or any(not isinstance(x,HistoricalReplayEvent) for x in self.events):raise HistoricalReplayValidationError("events must be immutable")
        object.__setattr__(self,"started_at",_time(self.started_at,"started_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at",True))
        if self.completed_at is not None and self.completed_at<self.started_at:raise HistoricalReplayValidationError("completed_at cannot precede started_at")
        if not isinstance(self.initial_paper_account,PaperTradingAccount) or self.initial_paper_account.account_id!=self.identity.account_id:raise HistoricalReplayValidationError("initial paper account identity mismatch")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"identity":self.identity.to_dict(),"events":[x.to_dict() for x in self.events],"started_at":self.started_at.isoformat(),"initial_paper_account":self.initial_paper_account.to_dict(),"completed_at":self.completed_at.isoformat() if self.completed_at else None,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(HistoricalReplayIdentity.from_dict(v["identity"]),tuple(HistoricalReplayEvent.from_dict(x) for x in v["events"]),datetime.fromisoformat(v["started_at"]),PaperTradingAccount.from_dict(v["initial_paper_account"]),datetime.fromisoformat(v["completed_at"]) if v.get("completed_at") else None,v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class HistoricalReplayEventResult:
    replay_id:str;event_id:str;sequence:int;symbol:str;event_time:datetime;status:HistoricalReplayEventStatus;orchestrator_request_id:str|None=None;orchestrator_result:PaperTradingCycleResult|None=None;resulting_state:PaperTradingAccount|None=None;reasons:tuple[str,...]=();warnings:tuple[str,...]=();errors:tuple[str,...]=();failed_stage:str|None=None;exception_type:str|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        for n in ("replay_id","event_id"):object.__setattr__(self,n,_text(getattr(self,n),n))
        if isinstance(self.sequence,bool) or not isinstance(self.sequence,int) or self.sequence<0:raise HistoricalReplayValidationError("event result sequence invalid")
        object.__setattr__(self,"symbol",_text(self.symbol,"symbol").upper());object.__setattr__(self,"event_time",_time(self.event_time,"event_time"))
        if not isinstance(self.status,HistoricalReplayEventStatus):raise HistoricalReplayValidationError("event result status invalid")
        object.__setattr__(self,"orchestrator_request_id",_text(self.orchestrator_request_id,"orchestrator_request_id",True))
        if self.orchestrator_result is not None and not isinstance(self.orchestrator_result,PaperTradingCycleResult):raise HistoricalReplayValidationError("orchestrator_result invalid")
        if self.resulting_state is not None and not isinstance(self.resulting_state,PaperTradingAccount):raise HistoricalReplayValidationError("resulting_state invalid")
        for n in ("reasons","warnings","errors"):object.__setattr__(self,n,_strings(getattr(self,n),n))
        for n in ("failed_stage","exception_type"):object.__setattr__(self,n,_text(getattr(self,n),n,True))
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"replay_id":self.replay_id,"event_id":self.event_id,"sequence":self.sequence,"symbol":self.symbol,"event_time":self.event_time.isoformat(),"status":self.status.value,"orchestrator_request_id":self.orchestrator_request_id,"orchestrator_result":self.orchestrator_result.to_dict() if self.orchestrator_result else None,"resulting_state":self.resulting_state.to_dict() if self.resulting_state else None,"reasons":list(self.reasons),"warnings":list(self.warnings),"errors":list(self.errors),"failed_stage":self.failed_stage,"exception_type":self.exception_type,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(v["replay_id"],v["event_id"],v["sequence"],v["symbol"],datetime.fromisoformat(v["event_time"]),HistoricalReplayEventStatus(v["status"]),v.get("orchestrator_request_id"),PaperTradingCycleResult.from_dict(v["orchestrator_result"]) if v.get("orchestrator_result") else None,PaperTradingAccount.from_dict(v["resulting_state"]) if v.get("resulting_state") else None,tuple(v.get("reasons",())),tuple(v.get("warnings",())),tuple(v.get("errors",())),v.get("failed_stage"),v.get("exception_type"),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class HistoricalReplayProgress:
    total_events:int;processed_events:int;completed_events:int;rejected_events:int;skipped_events:int;failed_events:int;last_processed_sequence:int|None=None;last_processed_event_id:str|None=None
    def __post_init__(self):
        for n in ("total_events","processed_events","completed_events","rejected_events","skipped_events","failed_events"):
            if isinstance(getattr(self,n),bool) or not isinstance(getattr(self,n),int) or getattr(self,n)<0:raise HistoricalReplayValidationError("progress counts invalid")
        if self.completed_events+self.rejected_events+self.failed_events!=self.processed_events or self.processed_events+self.skipped_events>self.total_events:raise HistoricalReplayValidationError("progress counts inconsistent")
        if self.last_processed_sequence is not None and (isinstance(self.last_processed_sequence,bool) or self.last_processed_sequence<0):raise HistoricalReplayValidationError("last processed sequence invalid")
        object.__setattr__(self,"last_processed_event_id",_text(self.last_processed_event_id,"last_processed_event_id",True))
    def to_dict(self):return {n:getattr(self,n) for n in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls,v):return cls(**dict(v))
@dataclass(frozen=True,slots=True)
class HistoricalReplayCriteriaResult:
    criterion:str;passed:bool;reasons:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"criterion",_text(self.criterion,"criterion"))
        if not isinstance(self.passed,bool):raise HistoricalReplayValidationError("passed must be boolean")
        object.__setattr__(self,"reasons",_strings(self.reasons,"reasons"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"criterion":self.criterion,"passed":self.passed,"reasons":list(self.reasons),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(v["criterion"],v["passed"],tuple(v.get("reasons",())),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class HistoricalReplayResult:
    identity:HistoricalReplayIdentity;status:HistoricalReplayStatus;event_results:tuple[HistoricalReplayEventResult,...];progress:HistoricalReplayProgress;final_state:PaperTradingAccount|None;started_at:datetime;completed_at:datetime|None;criteria_results:tuple[HistoricalReplayCriteriaResult,...];warnings:tuple[str,...]=();errors:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict);disabled:bool=False
    def __post_init__(self):
        if not isinstance(self.identity,HistoricalReplayIdentity) or not isinstance(self.status,HistoricalReplayStatus) or not isinstance(self.progress,HistoricalReplayProgress):raise HistoricalReplayValidationError("replay result core invalid")
        if not isinstance(self.event_results,tuple) or any(not isinstance(x,HistoricalReplayEventResult) for x in self.event_results):raise HistoricalReplayValidationError("event_results must be immutable")
        if self.status is HistoricalReplayStatus.DISABLED:
            if self.event_results:raise HistoricalReplayValidationError("disabled replay cannot expose event results")
        elif len(self.event_results)!=self.progress.total_events:raise HistoricalReplayValidationError("event result count mismatch")
        if self.event_results:
            completed=sum(x.status is HistoricalReplayEventStatus.COMPLETED for x in self.event_results);rejected=sum(x.status is HistoricalReplayEventStatus.REJECTED for x in self.event_results);failed=sum(x.status is HistoricalReplayEventStatus.FAILED for x in self.event_results);skipped=sum(x.status is HistoricalReplayEventStatus.SKIPPED for x in self.event_results)
            if (completed,rejected,failed,skipped)!=(self.progress.completed_events,self.progress.rejected_events,self.progress.failed_events,self.progress.skipped_events):raise HistoricalReplayValidationError("result progress does not match event results")
            valid=next((x for x in reversed(self.event_results) if x.status in (HistoricalReplayEventStatus.COMPLETED,HistoricalReplayEventStatus.REJECTED)),None)
            if valid is not None and self.final_state!=valid.resulting_state:raise HistoricalReplayValidationError("final state must match final valid event")
            expected=HistoricalReplayStatus.COMPLETED if failed==0 else HistoricalReplayStatus.PARTIALLY_COMPLETED if completed+rejected else HistoricalReplayStatus.FAILED
            if self.status is not expected:raise HistoricalReplayValidationError("replay status inconsistent with event results")
        elif self.status not in (HistoricalReplayStatus.DISABLED,HistoricalReplayStatus.EMPTY):raise HistoricalReplayValidationError("empty event results require disabled or empty status")
        if self.final_state is not None and (not isinstance(self.final_state,PaperTradingAccount) or self.final_state.account_id!=self.identity.account_id):raise HistoricalReplayValidationError("final state identity mismatch")
        object.__setattr__(self,"started_at",_time(self.started_at,"started_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at",True))
        if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,HistoricalReplayCriteriaResult) for x in self.criteria_results):raise HistoricalReplayValidationError("criteria invalid")
        for n in ("warnings","errors"):object.__setattr__(self,n,_strings(getattr(self,n),n))
        if not isinstance(self.disabled,bool) or (self.status is HistoricalReplayStatus.DISABLED)!=self.disabled:raise HistoricalReplayValidationError("disabled status inconsistent")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"event_results":[x.to_dict() for x in self.event_results],"progress":self.progress.to_dict(),"final_state":self.final_state.to_dict() if self.final_state else None,"started_at":self.started_at.isoformat(),"completed_at":self.completed_at.isoformat() if self.completed_at else None,"criteria_results":[x.to_dict() for x in self.criteria_results],"warnings":list(self.warnings),"errors":list(self.errors),"metadata":thaw_json_value(self.metadata),"disabled":self.disabled}
    @classmethod
    def from_dict(cls,v):return cls(HistoricalReplayIdentity.from_dict(v["identity"]),HistoricalReplayStatus(v["status"]),tuple(HistoricalReplayEventResult.from_dict(x) for x in v["event_results"]),HistoricalReplayProgress.from_dict(v["progress"]),PaperTradingAccount.from_dict(v["final_state"]) if v.get("final_state") else None,datetime.fromisoformat(v["started_at"]),datetime.fromisoformat(v["completed_at"]) if v.get("completed_at") else None,tuple(HistoricalReplayCriteriaResult.from_dict(x) for x in v["criteria_results"]),tuple(v.get("warnings",())),tuple(v.get("errors",())),v.get("metadata",{}),v.get("disabled",False))
