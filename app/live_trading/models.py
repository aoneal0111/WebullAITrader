"""Immutable Live Trading boundary contracts; research never constructs orders."""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from app.broker import BrokerOrderRequest,BrokerOrderResult,serialize_broker_order_request,serialize_broker_order_result
from app.research_portfolio import ResearchPortfolioRequest,ResearchPortfolioResult,serialize_request as serialize_research_request,serialize_result as serialize_research_result
from app.live_trading.exceptions import LiveTradingValidationError
def _text(value,name,optional=False):
    if optional and value is None:return None
    if not isinstance(value,str) or not value.strip() or value!=value.strip():raise LiveTradingValidationError(f"{name} must be a non-empty stripped string")
    return value
def _time(value,name):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:raise LiveTradingValidationError(f"{name} must be timezone-aware")
    return value
def _strings(value,name):
    if not isinstance(value,tuple) or any(not isinstance(x,str) or not x.strip() for x in value):raise LiveTradingValidationError(f"{name} must be immutable strings")
    return value
class LiveTradingStatus(StrEnum):
    COMPLETED="completed";PARTIALLY_COMPLETED="partially_completed";EMPTY="empty";DISABLED="disabled";REJECTED="rejected";FAILED="failed"
class LiveTradingResearchStatus(StrEnum):
    COMPLETED="completed";RESEARCH_FAILED="research_failed"
class LiveTradingOrderStatus(StrEnum):
    COMPLETED="completed";ORDER_FAILED="order_failed";SKIPPED="skipped"
@dataclass(frozen=True,slots=True)
class LiveTradingPolicy:
    enabled:bool=False;fail_fast:bool=True
    def __post_init__(self):
        if not isinstance(self.enabled,bool) or not isinstance(self.fail_fast,bool):raise LiveTradingValidationError("policy flags must be boolean")
    def to_dict(self):return {"enabled":self.enabled,"fail_fast":self.fail_fast}
    @classmethod
    def from_dict(cls,value):return cls(value.get("enabled",False),value.get("fail_fast",True))
@dataclass(frozen=True,slots=True)
class LiveTradingIdentity:
    live_trading_id:str
    def __post_init__(self):object.__setattr__(self,"live_trading_id",_text(self.live_trading_id,"live_trading_id"))
    def to_dict(self):return {"live_trading_id":self.live_trading_id}
@dataclass(frozen=True,slots=True)
class LiveTradingOrderIdentity:
    order_entry_id:str
    def __post_init__(self):object.__setattr__(self,"order_entry_id",_text(self.order_entry_id,"order_entry_id"))
    def to_dict(self):return {"order_entry_id":self.order_entry_id}
@dataclass(frozen=True,slots=True)
class LiveTradingOrderRequest:
    identity:LiveTradingOrderIdentity;broker_request:BrokerOrderRequest
    def __post_init__(self):
        if not isinstance(self.identity,LiveTradingOrderIdentity) or not isinstance(self.broker_request,BrokerOrderRequest):raise LiveTradingValidationError("order entry contracts are invalid")
    def to_dict(self):return {"identity":self.identity.to_dict(),"broker_request":serialize_broker_order_request(self.broker_request)}
@dataclass(frozen=True,slots=True)
class LiveTradingRequest:
    identity:LiveTradingIdentity;research_request:ResearchPortfolioRequest;orders:tuple[LiveTradingOrderRequest,...];policy:LiveTradingPolicy;requested_at:datetime;completed_at:datetime
    def __post_init__(self):
        if not isinstance(self.identity,LiveTradingIdentity) or not isinstance(self.research_request,ResearchPortfolioRequest) or not isinstance(self.orders,tuple) or any(not isinstance(x,LiveTradingOrderRequest) for x in self.orders) or not isinstance(self.policy,LiveTradingPolicy):raise LiveTradingValidationError("live trading request contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if self.completed_at<self.requested_at:raise LiveTradingValidationError("completed_at cannot precede requested_at")
    def to_dict(self):return {"identity":self.identity.to_dict(),"research_request":serialize_research_request(self.research_request),"orders":[x.to_dict() for x in self.orders],"policy":self.policy.to_dict(),"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat()}
@dataclass(frozen=True,slots=True)
class LiveTradingCriteriaResult:
    accepted:bool;errors:tuple[str,...]=()
    def __post_init__(self):
        if not isinstance(self.accepted,bool):raise LiveTradingValidationError("accepted must be boolean")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"))
    def to_dict(self):return {"accepted":self.accepted,"errors":list(self.errors)}
@dataclass(frozen=True,slots=True)
class LiveTradingResearchRecord:
    status:LiveTradingResearchStatus;research_request:ResearchPortfolioRequest;research_result:ResearchPortfolioResult|None;error_type:str|None=None;message:str|None=None
    def __post_init__(self):
        if not isinstance(self.status,LiveTradingResearchStatus) or not isinstance(self.research_request,ResearchPortfolioRequest):raise LiveTradingValidationError("research record contracts are invalid")
        if self.research_result is not None and not isinstance(self.research_result,ResearchPortfolioResult):raise LiveTradingValidationError("research_result is invalid")
        object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True));object.__setattr__(self,"message",_text(self.message,"message",True))
    def to_dict(self):return {"status":self.status.value,"research_request":serialize_research_request(self.research_request),"research_result":serialize_research_result(self.research_result) if self.research_result is not None else None,"error_type":self.error_type,"message":self.message}
@dataclass(frozen=True,slots=True)
class LiveTradingOrderRecord:
    index:int;identity:LiveTradingOrderIdentity;status:LiveTradingOrderStatus;broker_request:BrokerOrderRequest;broker_result:BrokerOrderResult|None;error_type:str|None=None;message:str|None=None
    def __post_init__(self):
        if isinstance(self.index,bool) or not isinstance(self.index,int) or self.index<0:raise LiveTradingValidationError("index must be non-negative integer")
        if not isinstance(self.identity,LiveTradingOrderIdentity) or not isinstance(self.status,LiveTradingOrderStatus) or not isinstance(self.broker_request,BrokerOrderRequest):raise LiveTradingValidationError("order record contracts are invalid")
        if self.broker_result is not None and not isinstance(self.broker_result,BrokerOrderResult):raise LiveTradingValidationError("broker_result is invalid")
        object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True));object.__setattr__(self,"message",_text(self.message,"message",True))
    def to_dict(self):return {"index":self.index,"identity":self.identity.to_dict(),"status":self.status.value,"broker_request":serialize_broker_order_request(self.broker_request),"broker_result":serialize_broker_order_result(self.broker_result) if self.broker_result is not None else None,"error_type":self.error_type,"message":self.message}
@dataclass(frozen=True,slots=True)
class LiveTradingSummary:
    total_orders:int;processed_orders:int;completed_orders:int;failed_orders:int;skipped_orders:int
    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,int) or value<0:raise LiveTradingValidationError("summary counts must be non-negative integers")
        if self.completed_orders+self.failed_orders+self.skipped_orders!=self.total_orders or self.processed_orders!=self.completed_orders+self.failed_orders:raise LiveTradingValidationError("summary counts are inconsistent")
    def to_dict(self):return {name:getattr(self,name) for name in self.__dataclass_fields__}
@dataclass(frozen=True,slots=True)
class LiveTradingResult:
    identity:LiveTradingIdentity;status:LiveTradingStatus;requested_at:datetime;completed_at:datetime;research:LiveTradingResearchRecord|None;orders:tuple[LiveTradingOrderRecord,...];summary:LiveTradingSummary;criteria:LiveTradingCriteriaResult;errors:tuple[str,...]=();error_type:str|None=None
    def __post_init__(self):
        if not isinstance(self.identity,LiveTradingIdentity) or not isinstance(self.status,LiveTradingStatus) or (self.research is not None and not isinstance(self.research,LiveTradingResearchRecord)) or not isinstance(self.summary,LiveTradingSummary) or not isinstance(self.criteria,LiveTradingCriteriaResult):raise LiveTradingValidationError("live trading result contracts are invalid")
        object.__setattr__(self,"requested_at",_time(self.requested_at,"requested_at"));object.__setattr__(self,"completed_at",_time(self.completed_at,"completed_at"))
        if not isinstance(self.orders,tuple) or any(not isinstance(x,LiveTradingOrderRecord) for x in self.orders):raise LiveTradingValidationError("order records must be immutable")
        if self.orders and tuple(x.index for x in self.orders)!=tuple(range(len(self.orders))):raise LiveTradingValidationError("order record indexes are invalid")
        object.__setattr__(self,"errors",_strings(self.errors,"errors"));object.__setattr__(self,"error_type",_text(self.error_type,"error_type",True))
    def to_dict(self):return {"identity":self.identity.to_dict(),"status":self.status.value,"requested_at":self.requested_at.isoformat(),"completed_at":self.completed_at.isoformat(),"research":self.research.to_dict() if self.research is not None else None,"orders":[x.to_dict() for x in self.orders],"summary":self.summary.to_dict(),"criteria":self.criteria.to_dict(),"errors":list(self.errors),"error_type":self.error_type}
