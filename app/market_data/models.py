from dataclasses import dataclass,field
from datetime import datetime
from decimal import Decimal,InvalidOperation
from enum import StrEnum
from typing import Mapping
from app.committee.models import JSONValue,freeze_json_mapping,thaw_json_value
from app.market_data.exceptions import MarketDataValidationError

# Existing event-stream protocol models are retained alongside the retrieval runtime models.
class MarketEventType(StrEnum):
 QUOTE="QUOTE";TRADE="TRADE";BOOK_SNAPSHOT="BOOK_SNAPSHOT";BOOK_DELTA="BOOK_DELTA";MARKET_STATUS="MARKET_STATUS";TRADING_HALT="TRADING_HALT";RESUME="RESUME";SYMBOL_METADATA="SYMBOL_METADATA";CORPORATE_ACTION="CORPORATE_ACTION";SESSION_CHANGE="SESSION_CHANGE";HEARTBEAT="HEARTBEAT";CLOCK_SYNC="CLOCK_SYNC"
class MarketSession(StrEnum):
 PRE_MARKET="PRE_MARKET";REGULAR="REGULAR";AFTER_HOURS="AFTER_HOURS";CLOSED="CLOSED";HOLIDAY="HOLIDAY";HALTED="HALTED"
class CorporateActionType(StrEnum):
 SPLIT="SPLIT";REVERSE_SPLIT="REVERSE_SPLIT";DIVIDEND="DIVIDEND";SYMBOL_CHANGE="SYMBOL_CHANGE";MERGER="MERGER";DELISTING="DELISTING"
@dataclass(frozen=True,slots=True)
class BookLevel:price:Decimal;size:Decimal
@dataclass(frozen=True,slots=True)
class QuotePayload:bid:Decimal;ask:Decimal;bid_size:Decimal;ask_size:Decimal
@dataclass(frozen=True,slots=True)
class TradePayload:price:Decimal;size:Decimal;trade_id:str
@dataclass(frozen=True,slots=True)
class OrderBookSnapshotPayload:bids:tuple[BookLevel,...];asks:tuple[BookLevel,...]
@dataclass(frozen=True,slots=True)
class OrderBookDeltaPayload:side:str;price:Decimal;size:Decimal;operation:str
@dataclass(frozen=True,slots=True)
class MarketStatusPayload:status:str
@dataclass(frozen=True,slots=True)
class TradingHaltPayload:reason:str
@dataclass(frozen=True,slots=True)
class ResumePayload:reason:str
@dataclass(frozen=True,slots=True)
class SymbolMetadataPayload:exchange:str;currency:str;tick_size:Decimal
@dataclass(frozen=True,slots=True)
class CorporateActionPayload:
 action_type:CorporateActionType;effective_timestamp:datetime;ratio:Decimal|None=None;cash_amount:Decimal|None=None;new_symbol:str|None=None
@dataclass(frozen=True,slots=True)
class SessionChangePayload:session:MarketSession
@dataclass(frozen=True,slots=True)
class HeartbeatPayload:connection_id:str
@dataclass(frozen=True,slots=True)
class ClockSyncPayload:exchange_timestamp:datetime;local_timestamp:datetime
EventPayload=QuotePayload|TradePayload|OrderBookSnapshotPayload|OrderBookDeltaPayload|MarketStatusPayload|TradingHaltPayload|ResumePayload|SymbolMetadataPayload|CorporateActionPayload|SessionChangePayload|HeartbeatPayload|ClockSyncPayload
@dataclass(frozen=True,slots=True)
class MarketEvent:sequence:int;timestamp:datetime;symbol:str|None;source:str;event_type:MarketEventType;payload:EventPayload
@dataclass(frozen=True,slots=True)
class MarketEventLog:events:tuple[MarketEvent,...]=();schema_version:int=1
@dataclass(frozen=True,slots=True)
class ClockMeasurement:exchange_timestamp:datetime;local_timestamp:datetime;latency_microseconds:int;clock_skew_microseconds:int
def _text(value,name):
 if not isinstance(value,str) or not value.strip() or value!=value.strip():raise MarketDataValidationError(f"{name} must be a non-empty stripped string")
 return value
def _price(value,name,required=False):
 if value is None:
  if required:raise MarketDataValidationError(f"{name} is required")
  return None
 if isinstance(value,bool) or not isinstance(value,(Decimal,str,int)):raise MarketDataValidationError(f"{name} must be Decimal-compatible")
 try:result=Decimal(value)
 except (InvalidOperation,ValueError) as exc:raise MarketDataValidationError(f"{name} must be a finite Decimal") from exc
 if not result.is_finite() or result<0 or (required and result==0):raise MarketDataValidationError(f"{name} must be {'positive' if required else 'non-negative'} and finite")
 return result
class MarketDataDecision(StrEnum):
 DISABLED="DISABLED";SESSION_INVALID="SESSION_INVALID";GATEWAY_FAILURE="GATEWAY_FAILURE";SUCCESS="SUCCESS"
@dataclass(frozen=True,slots=True)
class QuoteModel:
 symbol:str
 asset_type:str
 last_price:Decimal
 bid_price:Decimal|None=None
 ask_price:Decimal|None=None
 open_price:Decimal|None=None
 high_price:Decimal|None=None
 low_price:Decimal|None=None
 previous_close:Decimal|None=None
 volume:int|None=None
 currency:str="USD"
 metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"symbol",_text(self.symbol,"symbol").upper());object.__setattr__(self,"asset_type",_text(self.asset_type,"asset_type"));object.__setattr__(self,"currency",_text(self.currency,"currency").upper())
  object.__setattr__(self,"last_price",_price(self.last_price,"last_price",True))
  for name in ("bid_price","ask_price","open_price","high_price","low_price","previous_close"):object.__setattr__(self,name,_price(getattr(self,name),name))
  if self.volume is not None and (isinstance(self.volume,bool) or not isinstance(self.volume,int) or self.volume<0):raise MarketDataValidationError("volume must be a non-negative integer")
  if self.bid_price is not None and self.ask_price is not None and self.bid_price>self.ask_price:raise MarketDataValidationError("bid_price cannot exceed ask_price")
  if self.low_price is not None and self.high_price is not None and self.low_price>self.high_price:raise MarketDataValidationError("low_price cannot exceed high_price")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):
  return {"symbol":self.symbol,"asset_type":self.asset_type,"last_price":str(self.last_price),**{n:str(getattr(self,n)) if getattr(self,n) is not None else None for n in ("bid_price","ask_price","open_price","high_price","low_price","previous_close")},"volume":self.volume,"currency":self.currency,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:return cls(**dict(value))
  except MarketDataValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise MarketDataValidationError("invalid quote") from exc
@dataclass(frozen=True,slots=True)
class MarketDataRequest:
 request_id:str
 session_id:str
 symbols:tuple[str,...]
 metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"session_id",_text(self.session_id,"session_id"))
  if not isinstance(self.symbols,tuple) or not self.symbols:raise MarketDataValidationError("symbols must be a non-empty immutable tuple")
  symbols=tuple(_text(x,"symbol").upper() for x in self.symbols)
  if len(set(symbols))!=len(symbols):raise MarketDataValidationError("symbols must be unique")
  object.__setattr__(self,"symbols",symbols);object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"request_id":self.request_id,"session_id":self.session_id,"symbols":list(self.symbols),"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["symbols"]=tuple(data["symbols"]);return cls(**data)
  except MarketDataValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise MarketDataValidationError("invalid market data request") from exc
@dataclass(frozen=True,slots=True)
class MarketDataCriteriaResult:
 name:str;passed:bool;detail:str;metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"name",_text(self.name,"criteria name"));object.__setattr__(self,"detail",_text(self.detail,"criteria detail"))
  if not isinstance(self.passed,bool):raise MarketDataValidationError("criteria passed must be boolean")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 def to_dict(self):return {"name":self.name,"passed":self.passed,"detail":self.detail,"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):return cls(**dict(value))
@dataclass(frozen=True,slots=True)
class MarketDataResult:
 request_id:str;session_id:str;decision:MarketDataDecision;quotes:tuple[QuoteModel,...];criteria_results:tuple[MarketDataCriteriaResult,...];metadata:Mapping[str,JSONValue]=field(default_factory=dict)
 def __post_init__(self):
  object.__setattr__(self,"request_id",_text(self.request_id,"request_id"));object.__setattr__(self,"session_id",_text(self.session_id,"session_id"))
  if not isinstance(self.decision,MarketDataDecision):raise MarketDataValidationError("decision must be MarketDataDecision")
  if not isinstance(self.quotes,tuple) or any(not isinstance(x,QuoteModel) for x in self.quotes):raise MarketDataValidationError("quotes must be an immutable quote tuple")
  if len({x.symbol for x in self.quotes})!=len(self.quotes):raise MarketDataValidationError("quote symbols must be unique")
  if self.decision is not MarketDataDecision.SUCCESS and self.quotes:raise MarketDataValidationError("failure result cannot expose quotes")
  if not isinstance(self.criteria_results,tuple) or any(not isinstance(x,MarketDataCriteriaResult) for x in self.criteria_results):raise MarketDataValidationError("criteria_results must be immutable criteria tuple")
  object.__setattr__(self,"metadata",freeze_json_mapping("metadata",self.metadata))
 @property
 def success(self):return self.decision is MarketDataDecision.SUCCESS
 def to_dict(self):return {"request_id":self.request_id,"session_id":self.session_id,"decision":self.decision.value,"quotes":[x.to_dict() for x in self.quotes],"criteria_results":[x.to_dict() for x in self.criteria_results],"metadata":thaw_json_value(self.metadata)}
 @classmethod
 def from_dict(cls,value):
  try:data=dict(value);data["decision"]=MarketDataDecision(data["decision"]);data["quotes"]=tuple(QuoteModel.from_dict(x) for x in data["quotes"]);data["criteria_results"]=tuple(MarketDataCriteriaResult.from_dict(x) for x in data["criteria_results"]);return cls(**data)
  except MarketDataValidationError:raise
  except (TypeError,ValueError,KeyError) as exc:raise MarketDataValidationError("invalid market data result") from exc
