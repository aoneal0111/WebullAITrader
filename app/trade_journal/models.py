from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal,InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.trading_cycle import TradingCycle,TradingCycleMode,TradingCycleOutcome,TradingCycleStage
from app.trade_journal.exceptions import TradeJournalValidationError
def _text(v,n,optional=False):
    if optional and v is None:return None
    if not isinstance(v,str) or not v.strip() or v!=v.strip():raise TradeJournalValidationError(f"{n} must be a non-empty stripped string")
    return v
def _decimal(v,n):
    if v is None:return None
    if isinstance(v,bool) or not isinstance(v,(Decimal,str,int)):raise TradeJournalValidationError(f"{n} must be Decimal-compatible")
    try:r=Decimal(v)
    except (InvalidOperation,ValueError) as exc:raise TradeJournalValidationError(f"{n} must be finite") from exc
    if not r.is_finite():raise TradeJournalValidationError(f"{n} must be finite")
    return r
def _time(v,n):
    if not isinstance(v,datetime) or v.tzinfo is None or v.utcoffset() is None:raise TradeJournalValidationError(f"{n} must be timezone-aware")
    return v
def _strings(v,n):
    if not isinstance(v,tuple) or any(not isinstance(x,str) or not x.strip() for x in v):raise TradeJournalValidationError(f"{n} must be immutable strings")
    return v
class TradeJournalEntryType(StrEnum):EXECUTION="EXECUTION";PARTIAL_EXECUTION="PARTIAL_EXECUTION";NO_ACTION="NO_ACTION";REJECTION="REJECTION";FAILURE="FAILURE";DISABLED="DISABLED"
class TradeJournalStatus(StrEnum):ACTIVE="ACTIVE";ARCHIVED="ARCHIVED"
@dataclass(frozen=True,slots=True)
class TradeJournalIdentity:
    journal_id:str
    def __post_init__(self):object.__setattr__(self,"journal_id",_text(self.journal_id,"journal_id"))
    def to_dict(self):return {"journal_id":self.journal_id}
    @classmethod
    def from_dict(cls,v):return cls(v["journal_id"])
@dataclass(frozen=True,slots=True)
class TradeJournalEntry:
    entry_id:str;journal_id:str;cycle_id:str;request_id:str;account_id:str;symbol:str|None;mode:TradingCycleMode;cycle_outcome:TradingCycleOutcome;entry_type:TradeJournalEntryType;recorded_at:datetime;cycle_started_at:datetime;cycle_completed_at:datetime;strategy_signal:str|None=None;risk_outcome:str|None=None;planner_decision:str|None=None;execution_outcome:str|None=None;requested_quantity:Decimal|None=None;approved_quantity:Decimal|None=None;planned_quantity:Decimal|None=None;filled_quantity:Decimal|None=None;execution_price:Decimal|None=None;fees:Decimal|None=None;realized_profit_loss:Decimal|None=None;starting_equity:Decimal|None=None;ending_equity:Decimal|None=None;equity_change:Decimal|None=None;rejection_stage:TradingCycleStage|None=None;failed_stage:TradingCycleStage|None=None;reasons:tuple[str,...]=();warnings:tuple[str,...]=();errors:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        for n in ("entry_id","journal_id","cycle_id","request_id","account_id"):object.__setattr__(self,n,_text(getattr(self,n),n))
        object.__setattr__(self,"symbol",_text(self.symbol,"symbol",True).upper() if self.symbol else None)
        if not isinstance(self.mode,TradingCycleMode) or not isinstance(self.cycle_outcome,TradingCycleOutcome) or not isinstance(self.entry_type,TradeJournalEntryType):raise TradeJournalValidationError("entry enums are invalid")
        for n in ("recorded_at","cycle_started_at","cycle_completed_at"):object.__setattr__(self,n,_time(getattr(self,n),n))
        if self.cycle_completed_at<self.cycle_started_at or self.recorded_at<self.cycle_completed_at:raise TradeJournalValidationError("entry timestamps are out of order")
        for n in ("strategy_signal","risk_outcome","planner_decision","execution_outcome"):object.__setattr__(self,n,_text(getattr(self,n),n,True))
        for n in ("requested_quantity","approved_quantity","planned_quantity","filled_quantity","execution_price","fees","realized_profit_loss","starting_equity","ending_equity","equity_change"):object.__setattr__(self,n,_decimal(getattr(self,n),n))
        for n in ("rejection_stage","failed_stage"):
            if getattr(self,n) is not None and not isinstance(getattr(self,n),TradingCycleStage):raise TradeJournalValidationError(f"{n} must be TradingCycleStage")
        for n in ("reasons","warnings","errors"):object.__setattr__(self,n,_strings(getattr(self,n),n))
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):
        d={}
        for n in self.__dataclass_fields__:
            v=getattr(self,n);d[n]=thaw_json_value(v) if n=="metadata" else list(v) if isinstance(v,tuple) else v.isoformat() if isinstance(v,datetime) else v.value if isinstance(v,StrEnum) else str(v) if isinstance(v,Decimal) else v
        return d
    @classmethod
    def from_dict(cls,v):
        d=dict(v);d["mode"]=TradingCycleMode(d["mode"]);d["cycle_outcome"]=TradingCycleOutcome(d["cycle_outcome"]);d["entry_type"]=TradeJournalEntryType(d["entry_type"])
        for n in ("recorded_at","cycle_started_at","cycle_completed_at"):d[n]=datetime.fromisoformat(d[n])
        for n in ("rejection_stage","failed_stage"):d[n]=TradingCycleStage(d[n]) if d.get(n) else None
        for n in ("reasons","warnings","errors"):d[n]=tuple(d.get(n,()))
        return cls(**d)
@dataclass(frozen=True,slots=True)
class TradeJournalSummary:
    total_cycles:int;executed_cycles:int;partial_cycles:int;no_action_cycles:int;rejected_cycles:int;failed_cycles:int;disabled_cycles:int;total_filled_quantity:Decimal|None;total_fees:Decimal|None;total_realized_profit_loss:Decimal|None;first_recorded_at:datetime|None;last_recorded_at:datetime|None
    def __post_init__(self):
        for n in ("total_cycles","executed_cycles","partial_cycles","no_action_cycles","rejected_cycles","failed_cycles","disabled_cycles"):
            if isinstance(getattr(self,n),bool) or not isinstance(getattr(self,n),int) or getattr(self,n)<0:raise TradeJournalValidationError("summary counts must be nonnegative integers")
        for n in ("total_filled_quantity","total_fees","total_realized_profit_loss"):object.__setattr__(self,n,_decimal(getattr(self,n),n))
        for n in ("first_recorded_at","last_recorded_at"):
            if getattr(self,n) is not None:object.__setattr__(self,n,_time(getattr(self,n),n))
    def to_dict(self):return {n:(v.isoformat() if isinstance(v,datetime) else str(v) if isinstance(v,Decimal) else v) for n in self.__dataclass_fields__ if (v:=getattr(self,n)) is not ...}
    @classmethod
    def from_dict(cls,v):
        d=dict(v)
        for n in ("first_recorded_at","last_recorded_at"):d[n]=datetime.fromisoformat(d[n]) if d.get(n) else None
        return cls(**d)
@dataclass(frozen=True,slots=True)
class TradeJournalState:
    journal_id:str;status:TradeJournalStatus;entries:tuple[TradeJournalEntry,...]=();total_entries:int=0;summary:TradeJournalSummary|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"journal_id",_text(self.journal_id,"journal_id"))
        if not isinstance(self.status,TradeJournalStatus):raise TradeJournalValidationError("status must be TradeJournalStatus")
        if not isinstance(self.entries,tuple) or any(not isinstance(x,TradeJournalEntry) for x in self.entries):raise TradeJournalValidationError("entries must be immutable")
        if self.total_entries!=len(self.entries):raise TradeJournalValidationError("total_entries must equal entry count")
        if any(x.journal_id!=self.journal_id for x in self.entries):raise TradeJournalValidationError("entry journal identity mismatch")
        if self.summary is not None and not isinstance(self.summary,TradeJournalSummary):raise TradeJournalValidationError("summary must be TradeJournalSummary")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"journal_id":self.journal_id,"status":self.status.value,"entries":[x.to_dict() for x in self.entries],"total_entries":self.total_entries,"summary":self.summary.to_dict() if self.summary else None,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(v["journal_id"],TradeJournalStatus(v["status"]),tuple(TradeJournalEntry.from_dict(x) for x in v["entries"]),v["total_entries"],TradeJournalSummary.from_dict(v["summary"]) if v.get("summary") else None,v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class TradeJournalAppendRequest:
    journal_id:str;entry_id:str;cycle:TradingCycle;state:TradeJournalState;recorded_at:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"journal_id",_text(self.journal_id,"journal_id"));object.__setattr__(self,"entry_id",_text(self.entry_id,"entry_id"))
        if not isinstance(self.cycle,TradingCycle) or not isinstance(self.state,TradeJournalState):raise TradeJournalValidationError("cycle and state are required")
        object.__setattr__(self,"recorded_at",_time(self.recorded_at,"recorded_at"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"journal_id":self.journal_id,"entry_id":self.entry_id,"cycle":self.cycle.to_dict(),"state":self.state.to_dict(),"recorded_at":self.recorded_at.isoformat(),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(v["journal_id"],v["entry_id"],TradingCycle.from_dict(v["cycle"]),TradeJournalState.from_dict(v["state"]),datetime.fromisoformat(v["recorded_at"]),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class TradeJournalCriteriaResult:
    name:str;passed:bool;detail:str
    def __post_init__(self):
        object.__setattr__(self,"name",_text(self.name,"name"));object.__setattr__(self,"detail",_text(self.detail,"detail"))
        if not isinstance(self.passed,bool):raise TradeJournalValidationError("passed must be boolean")
    def to_dict(self):return {"name":self.name,"passed":self.passed,"detail":self.detail}
    @classmethod
    def from_dict(cls,v):return cls(**dict(v))
@dataclass(frozen=True,slots=True)
class TradeJournalAppendResult:
    state:TradeJournalState;entry:TradeJournalEntry|None;appended:bool;disabled:bool;criteria_results:tuple[TradeJournalCriteriaResult,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.state,TradeJournalState) or (self.entry is not None and not isinstance(self.entry,TradeJournalEntry)) or not isinstance(self.appended,bool) or not isinstance(self.disabled,bool):raise TradeJournalValidationError("invalid append result")
        if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,TradeJournalCriteriaResult) for x in self.criteria_results):raise TradeJournalValidationError("criteria must be immutable")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"state":self.state.to_dict(),"entry":self.entry.to_dict() if self.entry else None,"appended":self.appended,"disabled":self.disabled,"criteria_results":[x.to_dict() for x in self.criteria_results],"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(TradeJournalState.from_dict(v["state"]),TradeJournalEntry.from_dict(v["entry"]) if v.get("entry") else None,v["appended"],v["disabled"],tuple(TradeJournalCriteriaResult.from_dict(x) for x in v["criteria_results"]),v.get("metadata",{}))
