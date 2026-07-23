from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal,InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.execution_orchestrator import PaperTradingCycleResult
from app.execution_planner import ExecutionPlanResult
from app.paper_trading import PaperExecutionResult,PaperTradingAccount
from app.portfolio import PortfolioSnapshot
from app.risk import RiskResult
from app.strategy import StrategyResult
from app.trading_cycle.exceptions import TradingCycleValidationError

def _text(value,name,optional=False):
    if optional and value is None:return None
    if not isinstance(value,str) or not value.strip() or value!=value.strip():raise TradingCycleValidationError(f"{name} must be a non-empty stripped string")
    return value
def _decimal(value,name):
    if value is None:return None
    if isinstance(value,bool) or not isinstance(value,(Decimal,str,int)):raise TradingCycleValidationError(f"{name} must be Decimal-compatible")
    try:result=Decimal(value)
    except (InvalidOperation,ValueError) as exc:raise TradingCycleValidationError(f"{name} must be finite") from exc
    if not result.is_finite():raise TradingCycleValidationError(f"{name} must be finite")
    return result
def _time(value,name,optional=False):
    if optional and value is None:return None
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:raise TradingCycleValidationError(f"{name} must be timezone-aware")
    return value
def _strings(value,name):
    if not isinstance(value,tuple) or any(not isinstance(x,str) or not x.strip() for x in value):raise TradingCycleValidationError(f"{name} must be an immutable string tuple")
    return value

class TradingCycleMode(StrEnum):PAPER="PAPER";BACKTEST="BACKTEST";LIVE="LIVE"
class TradingCycleOutcome(StrEnum):
    EXECUTED="EXECUTED";PARTIALLY_EXECUTED="PARTIALLY_EXECUTED";NO_ACTION="NO_ACTION";STRATEGY_REJECTED="STRATEGY_REJECTED";RISK_REJECTED="RISK_REJECTED";PLANNING_REJECTED="PLANNING_REJECTED";EXECUTION_REJECTED="EXECUTION_REJECTED";DISABLED="DISABLED";FAILED="FAILED"
class TradingCycleStage(StrEnum):INPUT="INPUT";PORTFOLIO="PORTFOLIO";STRATEGY="STRATEGY";RISK="RISK";PLANNING="PLANNING";EXECUTION="EXECUTION";COMPLETED="COMPLETED"
class TradingCycleStageStatus(StrEnum):NOT_STARTED="NOT_STARTED";COMPLETED="COMPLETED";SKIPPED="SKIPPED";REJECTED="REJECTED";FAILED="FAILED"

@dataclass(frozen=True,slots=True)
class TradingCycleIdentity:
    cycle_id:str;request_id:str;account_id:str;symbol:str|None;mode:TradingCycleMode
    def __post_init__(self):
        for n in ("cycle_id","request_id","account_id"):object.__setattr__(self,n,_text(getattr(self,n),n))
        object.__setattr__(self,"symbol",_text(self.symbol,"symbol",True).upper() if self.symbol else None)
        if not isinstance(self.mode,TradingCycleMode):raise TradingCycleValidationError("mode must be TradingCycleMode")
    def to_dict(self):return {"cycle_id":self.cycle_id,"request_id":self.request_id,"account_id":self.account_id,"symbol":self.symbol,"mode":self.mode.value}
    @classmethod
    def from_dict(cls,v):return cls(v["cycle_id"],v["request_id"],v["account_id"],v.get("symbol"),TradingCycleMode(v["mode"]))

@dataclass(frozen=True,slots=True)
class TradingCycleTiming:
    started_at:datetime;completed_at:datetime;market_timestamp:datetime|None=None;decision_timestamp:datetime|None=None;execution_timestamp:datetime|None=None
    def __post_init__(self):
        for n in ("started_at","completed_at"):object.__setattr__(self,n,_time(getattr(self,n),n))
        if self.completed_at<self.started_at:raise TradingCycleValidationError("completed_at cannot precede started_at")
        for n in ("market_timestamp","decision_timestamp","execution_timestamp"):
            value=_time(getattr(self,n),n,True);object.__setattr__(self,n,value)
            if value is not None and not self.started_at<=value<=self.completed_at:raise TradingCycleValidationError(f"{n} must fall within cycle timing")
    def to_dict(self):return {n:(getattr(self,n).isoformat() if getattr(self,n) else None) for n in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls,v):return cls(*(datetime.fromisoformat(v[n]) if v.get(n) else None for n in cls.__dataclass_fields__))

@dataclass(frozen=True,slots=True)
class TradingCycleStageRecord:
    stage:TradingCycleStage;status:TradingCycleStageStatus;outcome_code:str|None=None;reasons:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.stage,TradingCycleStage) or not isinstance(self.status,TradingCycleStageStatus):raise TradingCycleValidationError("stage record enums are invalid")
        object.__setattr__(self,"outcome_code",_text(self.outcome_code,"outcome_code",True));object.__setattr__(self,"reasons",_strings(self.reasons,"reasons"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"stage":self.stage.value,"status":self.status.value,"outcome_code":self.outcome_code,"reasons":list(self.reasons),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(TradingCycleStage(v["stage"]),TradingCycleStageStatus(v["status"]),v.get("outcome_code"),tuple(v.get("reasons",())),v.get("metadata",{}))

@dataclass(frozen=True,slots=True)
class TradingDecisionTrace:
    strategy_signal:str|None=None;strategy_confidence:Decimal|None=None;strategy_reasons:tuple[str,...]=();requested_quantity:Decimal|None=None;risk_outcome:str|None=None;approved_quantity:Decimal|None=None;risk_reasons:tuple[str,...]=();planner_decision:str|None=None;planned_side:str|None=None;planned_quantity:Decimal|None=None;planned_order_type:str|None=None;planned_time_in_force:str|None=None;planned_limit_price:Decimal|None=None;planned_stop_price:Decimal|None=None;execution_outcome:str|None=None;filled_quantity:Decimal|None=None;execution_price:Decimal|None=None;fees:Decimal|None=None;realized_profit_loss:Decimal|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        for n in ("strategy_signal","risk_outcome","planner_decision","planned_side","planned_order_type","planned_time_in_force","execution_outcome"):object.__setattr__(self,n,_text(getattr(self,n),n,True))
        for n in ("strategy_confidence","requested_quantity","approved_quantity","planned_quantity","planned_limit_price","planned_stop_price","filled_quantity","execution_price","fees","realized_profit_loss"):object.__setattr__(self,n,_decimal(getattr(self,n),n))
        object.__setattr__(self,"strategy_reasons",_strings(self.strategy_reasons,"strategy_reasons"));object.__setattr__(self,"risk_reasons",_strings(self.risk_reasons,"risk_reasons"));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):
        result={}
        for n in self.__dataclass_fields__:
            v=getattr(self,n);result[n]=thaw_json_value(v) if n=="metadata" else list(v) if isinstance(v,tuple) else str(v) if isinstance(v,Decimal) else v
        return result
    @classmethod
    def from_dict(cls,v):
        d=dict(v);d["strategy_reasons"]=tuple(d.get("strategy_reasons",()));d["risk_reasons"]=tuple(d.get("risk_reasons",()));return cls(**d)

@dataclass(frozen=True,slots=True)
class TradingCycleDiagnostics:
    warnings:tuple[str,...]=();errors:tuple[str,...]=();failed_stage:TradingCycleStage|None=None;rejection_stage:TradingCycleStage|None=None;exception_type:str|None=None;reason_codes:tuple[str,...]=();metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        for n in ("warnings","errors","reason_codes"):object.__setattr__(self,n,_strings(getattr(self,n),n))
        for n in ("failed_stage","rejection_stage"):
            if getattr(self,n) is not None and not isinstance(getattr(self,n),TradingCycleStage):raise TradingCycleValidationError(f"{n} must be TradingCycleStage")
        object.__setattr__(self,"exception_type",_text(self.exception_type,"exception_type",True));object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"warnings":list(self.warnings),"errors":list(self.errors),"failed_stage":self.failed_stage.value if self.failed_stage else None,"rejection_stage":self.rejection_stage.value if self.rejection_stage else None,"exception_type":self.exception_type,"reason_codes":list(self.reason_codes),"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(tuple(v.get("warnings",())),tuple(v.get("errors",())),TradingCycleStage(v["failed_stage"]) if v.get("failed_stage") else None,TradingCycleStage(v["rejection_stage"]) if v.get("rejection_stage") else None,v.get("exception_type"),tuple(v.get("reason_codes",())),v.get("metadata",{}))

@dataclass(frozen=True,slots=True)
class TradingCycleMetrics:
    starting_cash:Decimal|None=None;ending_cash:Decimal|None=None;starting_equity:Decimal|None=None;ending_equity:Decimal|None=None;equity_change:Decimal|None=None;requested_quantity:Decimal|None=None;approved_quantity:Decimal|None=None;planned_quantity:Decimal|None=None;filled_quantity:Decimal|None=None;execution_price:Decimal|None=None;total_fees:Decimal|None=None;realized_profit_loss:Decimal|None=None;unrealized_profit_loss_before:Decimal|None=None;unrealized_profit_loss_after:Decimal|None=None;market_value_before:Decimal|None=None;market_value_after:Decimal|None=None
    def __post_init__(self):
        for n in self.__dataclass_fields__:object.__setattr__(self,n,_decimal(getattr(self,n),n))
    def to_dict(self):return {n:(str(getattr(self,n)) if getattr(self,n) is not None else None) for n in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls,v):return cls(**dict(v))

@dataclass(frozen=True,slots=True)
class TradingCycle:
    identity:TradingCycleIdentity;timing:TradingCycleTiming;outcome:TradingCycleOutcome;portfolio_before:PortfolioSnapshot|None;portfolio_after:PortfolioSnapshot|None;original_account:PaperTradingAccount|None;resulting_account:PaperTradingAccount|None;stage_records:tuple[TradingCycleStageRecord,...];decision_trace:TradingDecisionTrace|None;diagnostics:TradingCycleDiagnostics|None;metrics:TradingCycleMetrics|None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.identity,TradingCycleIdentity) or not isinstance(self.timing,TradingCycleTiming) or not isinstance(self.outcome,TradingCycleOutcome):raise TradingCycleValidationError("cycle identity, timing, and outcome are required")
        if not isinstance(self.stage_records,tuple) or any(not isinstance(x,TradingCycleStageRecord) for x in self.stage_records):raise TradingCycleValidationError("stage_records must be immutable")
        if self.stage_records and tuple(x.stage for x in self.stage_records)!=tuple(TradingCycleStage):raise TradingCycleValidationError("stage_records must contain every stage exactly once in order")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"identity":self.identity.to_dict(),"timing":self.timing.to_dict(),"outcome":self.outcome.value,"portfolio_before":self.portfolio_before.to_dict() if self.portfolio_before else None,"portfolio_after":self.portfolio_after.to_dict() if self.portfolio_after else None,"original_account":self.original_account.to_dict() if self.original_account else None,"resulting_account":self.resulting_account.to_dict() if self.resulting_account else None,"stage_records":[x.to_dict() for x in self.stage_records],"decision_trace":self.decision_trace.to_dict() if self.decision_trace else None,"diagnostics":self.diagnostics.to_dict() if self.diagnostics else None,"metrics":self.metrics.to_dict() if self.metrics else None,"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(TradingCycleIdentity.from_dict(v["identity"]),TradingCycleTiming.from_dict(v["timing"]),TradingCycleOutcome(v["outcome"]),PortfolioSnapshot.from_dict(v["portfolio_before"]) if v.get("portfolio_before") else None,PortfolioSnapshot.from_dict(v["portfolio_after"]) if v.get("portfolio_after") else None,PaperTradingAccount.from_dict(v["original_account"]) if v.get("original_account") else None,PaperTradingAccount.from_dict(v["resulting_account"]) if v.get("resulting_account") else None,tuple(TradingCycleStageRecord.from_dict(x) for x in v["stage_records"]),TradingDecisionTrace.from_dict(v["decision_trace"]) if v.get("decision_trace") else None,TradingCycleDiagnostics.from_dict(v["diagnostics"]) if v.get("diagnostics") else None,TradingCycleMetrics.from_dict(v["metrics"]) if v.get("metrics") else None,v.get("metadata",{}))

@dataclass(frozen=True,slots=True)
class TradingCycleBuildRequest:
    cycle_id:str;request_id:str;account_id:str;mode:TradingCycleMode;started_at:datetime;completed_at:datetime;portfolio_before:PortfolioSnapshot|None;portfolio_after:PortfolioSnapshot|None=None;original_account:PaperTradingAccount|None=None;resulting_account:PaperTradingAccount|None=None;strategy_result:StrategyResult|None=None;risk_result:RiskResult|None=None;execution_plan_result:ExecutionPlanResult|None=None;paper_execution_result:PaperExecutionResult|None=None;orchestrator_result:PaperTradingCycleResult|None=None;market_timestamp:datetime|None=None;decision_timestamp:datetime|None=None;execution_timestamp:datetime|None=None;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        for n in ("cycle_id","request_id","account_id"):object.__setattr__(self,n,_text(getattr(self,n),n))
        if not isinstance(self.mode,TradingCycleMode):raise TradingCycleValidationError("mode must be TradingCycleMode")
        TradingCycleTiming(self.started_at,self.completed_at,self.market_timestamp,self.decision_timestamp,self.execution_timestamp)
        expected=(("portfolio_before",PortfolioSnapshot),("portfolio_after",PortfolioSnapshot),("original_account",PaperTradingAccount),("resulting_account",PaperTradingAccount),("strategy_result",StrategyResult),("risk_result",RiskResult),("execution_plan_result",ExecutionPlanResult),("paper_execution_result",PaperExecutionResult),("orchestrator_result",PaperTradingCycleResult))
        if any(getattr(self,n) is not None and not isinstance(getattr(self,n),t) for n,t in expected):raise TradingCycleValidationError("build artifact type is invalid")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):
        result={"cycle_id":self.cycle_id,"request_id":self.request_id,"account_id":self.account_id,"mode":self.mode.value,"started_at":self.started_at.isoformat(),"completed_at":self.completed_at.isoformat()}
        for n in ("portfolio_before","portfolio_after","original_account","resulting_account","strategy_result","risk_result","execution_plan_result","paper_execution_result","orchestrator_result"):
            value=getattr(self,n);result[n]=value.to_dict() if value else None
        for n in ("market_timestamp","decision_timestamp","execution_timestamp"):result[n]=getattr(self,n).isoformat() if getattr(self,n) else None
        result["metadata"]=thaw_json_value(self.metadata);return result
    @classmethod
    def from_dict(cls,v):
        d=dict(v);d["mode"]=TradingCycleMode(d["mode"])
        for n in ("started_at","completed_at","market_timestamp","decision_timestamp","execution_timestamp"):d[n]=datetime.fromisoformat(d[n]) if d.get(n) else None
        types={"portfolio_before":PortfolioSnapshot,"portfolio_after":PortfolioSnapshot,"original_account":PaperTradingAccount,"resulting_account":PaperTradingAccount,"strategy_result":StrategyResult,"risk_result":RiskResult,"execution_plan_result":ExecutionPlanResult,"paper_execution_result":PaperExecutionResult,"orchestrator_result":PaperTradingCycleResult}
        for n,t in types.items():d[n]=t.from_dict(d[n]) if d.get(n) else None
        return cls(**d)

@dataclass(frozen=True,slots=True)
class TradingCycleCriteriaResult:
    name:str;passed:bool;detail:str
    def __post_init__(self):object.__setattr__(self,"name",_text(self.name,"name"));object.__setattr__(self,"detail",_text(self.detail,"detail"));isinstance(self.passed,bool) or (_ for _ in ()).throw(TradingCycleValidationError("passed must be boolean"))
    def to_dict(self):return {"name":self.name,"passed":self.passed,"detail":self.detail}
    @classmethod
    def from_dict(cls,v):return cls(**dict(v))
@dataclass(frozen=True,slots=True)
class TradingCycleBuildResult:
    cycle:TradingCycle;criteria_results:tuple[TradingCycleCriteriaResult,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
    def __post_init__(self):
        if not isinstance(self.cycle,TradingCycle) or not isinstance(self.criteria_results,tuple) or any(not isinstance(x,TradingCycleCriteriaResult) for x in self.criteria_results):raise TradingCycleValidationError("invalid build result")
        object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
    def to_dict(self):return {"cycle":self.cycle.to_dict(),"criteria_results":[x.to_dict() for x in self.criteria_results],"metadata":thaw_json_value(self.metadata)}
    @classmethod
    def from_dict(cls,v):return cls(TradingCycle.from_dict(v["cycle"]),tuple(TradingCycleCriteriaResult.from_dict(x) for x in v["criteria_results"]),v.get("metadata",{}))
