from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal,InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.trade_journal import TradeJournalState
from app.analytics.exceptions import AnalyticsValidationError
def _text(v,n):
    if not isinstance(v,str) or not v.strip() or v!=v.strip():raise AnalyticsValidationError(f"{n} must be a non-empty stripped string")
    return v
def _decimal(v,n,nonnegative=False):
    if v is None:return None
    if isinstance(v,bool) or not isinstance(v,(Decimal,str,int)):raise AnalyticsValidationError(f"{n} must be Decimal-compatible")
    try:r=Decimal(v)
    except (InvalidOperation,ValueError) as exc:raise AnalyticsValidationError(f"{n} must be finite") from exc
    if not r.is_finite() or (nonnegative and r<0):raise AnalyticsValidationError(f"{n} must be finite"+(" and non-negative" if nonnegative else ""))
    return r
def _time(v,n,optional=False):
    if optional and v is None:return None
    if not isinstance(v,datetime) or v.tzinfo is None or v.utcoffset() is None:raise AnalyticsValidationError(f"{n} must be timezone-aware")
    return v
def _strings(v,n):
    if not isinstance(v,tuple) or any(not isinstance(x,str) or not x.strip() for x in v):raise AnalyticsValidationError(f"{n} must be immutable strings")
    return v
class AnalyticsStatus(StrEnum):COMPLETED="COMPLETED";DISABLED="DISABLED";INSUFFICIENT_DATA="INSUFFICIENT_DATA"
class AnalyticsEntryClassification(StrEnum):WIN="WIN";LOSS="LOSS";BREAKEVEN="BREAKEVEN";UNCLASSIFIED="UNCLASSIFIED"
class DrawdownStatus(StrEnum):AT_PEAK="AT_PEAK";IN_DRAWDOWN="IN_DRAWDOWN"
@dataclass(frozen=True,slots=True)
class AnalyticsRequest:
    request_id:str;journal:TradeJournalState;as_of:datetime;metadata:Mapping[str,JSONValue]=field(default_factory=dict);starting_equity:Decimal|None=None
    def __post_init__(self):
        object.__setattr__(self,"request_id",_text(self.request_id,"request_id"))
        if not isinstance(self.journal,TradeJournalState):raise AnalyticsValidationError("journal must be TradeJournalState")
        object.__setattr__(self,"as_of",_time(self.as_of,"as_of"));object.__setattr__(self,"starting_equity",_decimal(self.starting_equity,"starting_equity",True));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"request_id":self.request_id,"journal":self.journal.to_dict(),"as_of":self.as_of.isoformat(),"metadata":thaw_json_value(self.metadata),"starting_equity":str(self.starting_equity) if self.starting_equity is not None else None}
    @classmethod
    def from_dict(cls,v):return cls(v["request_id"],TradeJournalState.from_dict(v["journal"]),datetime.fromisoformat(v["as_of"]),v.get("metadata",{}),v.get("starting_equity"))
@dataclass(frozen=True,slots=True)
class EquityPoint:
    sequence:int;entry_id:str;cycle_id:str;recorded_at:datetime;equity:Decimal;equity_change:Decimal|None;cumulative_net_profit:Decimal|None
    def __post_init__(self):
        if isinstance(self.sequence,bool) or not isinstance(self.sequence,int) or self.sequence<0:raise AnalyticsValidationError("sequence must be non-negative integer")
        object.__setattr__(self,"entry_id",_text(self.entry_id,"entry_id"));object.__setattr__(self,"cycle_id",_text(self.cycle_id,"cycle_id"));object.__setattr__(self,"recorded_at",_time(self.recorded_at,"recorded_at"));object.__setattr__(self,"equity",_decimal(self.equity,"equity",True));object.__setattr__(self,"equity_change",_decimal(self.equity_change,"equity_change"));object.__setattr__(self,"cumulative_net_profit",_decimal(self.cumulative_net_profit,"cumulative_net_profit"))
    def to_dict(self):return {"sequence":self.sequence,"entry_id":self.entry_id,"cycle_id":self.cycle_id,"recorded_at":self.recorded_at.isoformat(),"equity":str(self.equity),"equity_change":str(self.equity_change) if self.equity_change is not None else None,"cumulative_net_profit":str(self.cumulative_net_profit) if self.cumulative_net_profit is not None else None}
    @classmethod
    def from_dict(cls,v):return cls(**{**dict(v),"recorded_at":datetime.fromisoformat(v["recorded_at"])})
@dataclass(frozen=True,slots=True)
class DrawdownPoint:
    sequence:int;entry_id:str;cycle_id:str;recorded_at:datetime;equity:Decimal;peak_equity:Decimal;drawdown_amount:Decimal;drawdown_percentage:Decimal|None;status:DrawdownStatus
    def __post_init__(self):
        if isinstance(self.sequence,bool) or not isinstance(self.sequence,int) or self.sequence<0:raise AnalyticsValidationError("sequence must be non-negative integer")
        object.__setattr__(self,"entry_id",_text(self.entry_id,"entry_id"));object.__setattr__(self,"cycle_id",_text(self.cycle_id,"cycle_id"));object.__setattr__(self,"recorded_at",_time(self.recorded_at,"recorded_at"))
        for n in ("equity","peak_equity","drawdown_amount"):object.__setattr__(self,n,_decimal(getattr(self,n),n,True))
        object.__setattr__(self,"drawdown_percentage",_decimal(self.drawdown_percentage,"drawdown_percentage",True))
        if self.drawdown_amount!=self.peak_equity-self.equity or self.drawdown_amount<0:raise AnalyticsValidationError("drawdown amount is inconsistent")
        if (self.peak_equity==0 and self.drawdown_percentage is not None) or (self.peak_equity>0 and self.drawdown_percentage!=self.drawdown_amount/self.peak_equity):raise AnalyticsValidationError("drawdown percentage is inconsistent")
        if not isinstance(self.status,DrawdownStatus) or (self.status is DrawdownStatus.AT_PEAK)!=(self.drawdown_amount==0):raise AnalyticsValidationError("drawdown status is inconsistent")
    def to_dict(self):return {"sequence":self.sequence,"entry_id":self.entry_id,"cycle_id":self.cycle_id,"recorded_at":self.recorded_at.isoformat(),"equity":str(self.equity),"peak_equity":str(self.peak_equity),"drawdown_amount":str(self.drawdown_amount),"drawdown_percentage":str(self.drawdown_percentage) if self.drawdown_percentage is not None else None,"status":self.status.value}
    @classmethod
    def from_dict(cls,v):return cls(v["sequence"],v["entry_id"],v["cycle_id"],datetime.fromisoformat(v["recorded_at"]),v["equity"],v["peak_equity"],v["drawdown_amount"],v.get("drawdown_percentage"),DrawdownStatus(v["status"]))
@dataclass(frozen=True,slots=True)
class AnalyticsMetrics:
    total_entries:int;executed_entries:int;partial_execution_entries:int;no_action_entries:int;rejected_entries:int;failed_entries:int;disabled_entries:int;classified_trades:int;winning_trades:int;losing_trades:int;breakeven_trades:int;unclassified_trades:int
    win_rate:Decimal|None=None;loss_rate:Decimal|None=None;breakeven_rate:Decimal|None=None;gross_profit:Decimal|None=None;gross_loss:Decimal|None=None;net_profit:Decimal|None=None;average_trade:Decimal|None=None;average_winner:Decimal|None=None;average_loser:Decimal|None=None;largest_winner:Decimal|None=None;largest_loser:Decimal|None=None;profit_factor:Decimal|None=None;expectancy:Decimal|None=None;total_fees:Decimal|None=None;total_filled_quantity:Decimal|None=None;average_filled_quantity:Decimal|None=None;starting_equity:Decimal|None=None;ending_equity:Decimal|None=None;maximum_equity:Decimal|None=None;minimum_equity:Decimal|None=None;maximum_drawdown_amount:Decimal|None=None;maximum_drawdown_percentage:Decimal|None=None;current_drawdown_amount:Decimal|None=None;current_drawdown_percentage:Decimal|None=None;equity_change:Decimal|None=None;first_recorded_at:datetime|None=None;last_recorded_at:datetime|None=None
    def __post_init__(self):
        counts=("total_entries","executed_entries","partial_execution_entries","no_action_entries","rejected_entries","failed_entries","disabled_entries","classified_trades","winning_trades","losing_trades","breakeven_trades","unclassified_trades")
        for n in counts:
            if isinstance(getattr(self,n),bool) or not isinstance(getattr(self,n),int) or getattr(self,n)<0:raise AnalyticsValidationError("metric counts must be non-negative integers")
        if self.executed_entries+self.partial_execution_entries+self.no_action_entries+self.rejected_entries+self.failed_entries+self.disabled_entries!=self.total_entries:raise AnalyticsValidationError("entry category counts must sum to total_entries")
        if self.winning_trades+self.losing_trades+self.breakeven_trades!=self.classified_trades or self.classified_trades+self.unclassified_trades!=self.total_entries:raise AnalyticsValidationError("classification counts are inconsistent")
        nonnegative=("win_rate","loss_rate","breakeven_rate","gross_profit","average_winner","profit_factor","total_fees","total_filled_quantity","average_filled_quantity","starting_equity","ending_equity","maximum_equity","minimum_equity","maximum_drawdown_amount","maximum_drawdown_percentage","current_drawdown_amount","current_drawdown_percentage")
        decimal_names=tuple(n for n in self.__dataclass_fields__ if n not in counts and n not in ("first_recorded_at","last_recorded_at"))
        for n in decimal_names:object.__setattr__(self,n,_decimal(getattr(self,n),n,n in nonnegative))
        for n in ("win_rate","loss_rate","breakeven_rate"):
            if getattr(self,n) is not None and not Decimal("0")<=getattr(self,n)<=Decimal("1"):raise AnalyticsValidationError("rates must be decimal fractions")
        if self.gross_loss is not None and self.gross_loss>0:raise AnalyticsValidationError("gross_loss must be non-positive")
        if self.average_loser is not None and self.average_loser>=0:raise AnalyticsValidationError("average_loser must be negative")
        for n in ("first_recorded_at","last_recorded_at"):object.__setattr__(self,n,_time(getattr(self,n),n,True))
    def to_dict(self):return {n:(v.isoformat() if isinstance(v,datetime) else str(v) if isinstance(v,Decimal) else v) for n in self.__dataclass_fields__ for v in (getattr(self,n),)}
    @classmethod
    def from_dict(cls,v):
        d=dict(v)
        for n in ("first_recorded_at","last_recorded_at"):d[n]=datetime.fromisoformat(d[n]) if d.get(n) else None
        return cls(**d)
@dataclass(frozen=True,slots=True)
class AnalyticsSummary:
    request_id:str;journal_id:str;status:AnalyticsStatus;metrics:AnalyticsMetrics;equity_curve:tuple[EquityPoint,...];drawdown_curve:tuple[DrawdownPoint,...];warnings:tuple[str,...]=();diagnostics:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"journal_id",_text(self.journal_id,"journal_id"))
        if not isinstance(self.status,AnalyticsStatus) or self.status is AnalyticsStatus.DISABLED or not isinstance(self.metrics,AnalyticsMetrics):raise AnalyticsValidationError("summary status or metrics invalid")
        if not isinstance(self.equity_curve,tuple) or any(not isinstance(x,EquityPoint) for x in self.equity_curve) or tuple(x.sequence for x in self.equity_curve)!=tuple(range(len(self.equity_curve))):raise AnalyticsValidationError("equity curve sequence invalid")
        if not isinstance(self.drawdown_curve,tuple) or any(not isinstance(x,DrawdownPoint) for x in self.drawdown_curve) or tuple(x.sequence for x in self.drawdown_curve)!=tuple(range(len(self.drawdown_curve))):raise AnalyticsValidationError("drawdown curve sequence invalid")
        if len(self.drawdown_curve) not in (0,len(self.equity_curve)):raise AnalyticsValidationError("drawdown curve must align with equity curve")
        object.__setattr__(self,"warnings",_strings(self.warnings,"warnings"));object.__setattr__(self,"diagnostics",_strings(self.diagnostics,"diagnostics"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"request_id":self.request_id,"journal_id":self.journal_id,"status":self.status.value,"metrics":self.metrics.to_dict(),"equity_curve":[x.to_dict() for x in self.equity_curve],"drawdown_curve":[x.to_dict() for x in self.drawdown_curve],"warnings":list(self.warnings),"diagnostics":list(self.diagnostics),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(v["request_id"],v["journal_id"],AnalyticsStatus(v["status"]),AnalyticsMetrics.from_dict(v["metrics"]),tuple(EquityPoint.from_dict(x) for x in v["equity_curve"]),tuple(DrawdownPoint.from_dict(x) for x in v["drawdown_curve"]),tuple(v.get("warnings",())),tuple(v.get("diagnostics",())),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class AnalyticsCriteriaResult:
    criterion:str;passed:bool;reasons:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"criterion",_text(self.criterion,"criterion"))
        if not isinstance(self.passed,bool):raise AnalyticsValidationError("passed must be boolean")
        object.__setattr__(self,"reasons",_strings(self.reasons,"reasons"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"criterion":self.criterion,"passed":self.passed,"reasons":list(self.reasons),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(v["criterion"],v["passed"],tuple(v.get("reasons",())),v.get("metadata",{}))
@dataclass(frozen=True,slots=True)
class AnalyticsResult:
    request_id:str;journal_id:str;status:AnalyticsStatus;summary:AnalyticsSummary|None;criteria_results:tuple[AnalyticsCriteriaResult,...];warnings:tuple[str,...]=();errors:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict);disabled:bool=False
    def __post_init__(self):
        object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"journal_id",_text(self.journal_id,"journal_id"))
        if not isinstance(self.status,AnalyticsStatus) or not isinstance(self.disabled,bool):raise AnalyticsValidationError("result status invalid")
        if (self.status is AnalyticsStatus.DISABLED)!=(self.disabled is True) or (self.status is AnalyticsStatus.DISABLED)!=(self.summary is None):raise AnalyticsValidationError("disabled result shape invalid")
        if self.summary and (self.summary.request_id!=self.request_id or self.summary.journal_id!=self.journal_id or self.summary.status!=self.status):raise AnalyticsValidationError("summary identity mismatch")
        if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,AnalyticsCriteriaResult) for x in self.criteria_results):raise AnalyticsValidationError("criteria must be immutable")
        object.__setattr__(self,"warnings",_strings(self.warnings,"warnings"));object.__setattr__(self,"errors",_strings(self.errors,"errors"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"request_id":self.request_id,"journal_id":self.journal_id,"status":self.status.value,"summary":self.summary.to_dict() if self.summary else None,"criteria_results":[x.to_dict() for x in self.criteria_results],"warnings":list(self.warnings),"errors":list(self.errors),"metadata":thaw_json_value(self.metadata),"disabled":self.disabled}
    @classmethod
    def from_dict(cls,v):return cls(v["request_id"],v["journal_id"],AnalyticsStatus(v["status"]),AnalyticsSummary.from_dict(v["summary"]) if v.get("summary") else None,tuple(AnalyticsCriteriaResult.from_dict(x) for x in v["criteria_results"]),tuple(v.get("warnings",())),tuple(v.get("errors",())),v.get("metadata",{}),v.get("disabled",False))
