from datetime import datetime
from decimal import Decimal
import re
from app.market_data.exceptions import MarketDataDependencyError,MarketDataValidationError
from app.market_data.models import *
from app.market_data.policies import MarketDataPolicy
def validate_dependencies(session_manager,broker_gateway,policy):
 if session_manager is None or not callable(getattr(session_manager,"state",None)):raise MarketDataDependencyError("session manager must expose state()")
 if broker_gateway is None or not callable(getattr(broker_gateway,"get_market_data",None)):raise MarketDataDependencyError("broker market data gateway must expose get_market_data(request)")
 if not isinstance(policy,MarketDataPolicy):raise MarketDataDependencyError("policy must be MarketDataPolicy")
def validate_request(request):
 if not isinstance(request,MarketDataRequest):raise MarketDataValidationError("request must be MarketDataRequest")
 return request

SYMBOL=re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,31}$")
PAYLOADS={MarketEventType.QUOTE:QuotePayload,MarketEventType.TRADE:TradePayload,MarketEventType.BOOK_SNAPSHOT:OrderBookSnapshotPayload,MarketEventType.BOOK_DELTA:OrderBookDeltaPayload,MarketEventType.MARKET_STATUS:MarketStatusPayload,MarketEventType.TRADING_HALT:TradingHaltPayload,MarketEventType.RESUME:ResumePayload,MarketEventType.SYMBOL_METADATA:SymbolMetadataPayload,MarketEventType.CORPORATE_ACTION:CorporateActionPayload,MarketEventType.SESSION_CHANGE:SessionChangePayload,MarketEventType.HEARTBEAT:HeartbeatPayload,MarketEventType.CLOCK_SYNC:ClockSyncPayload}
SYMBOL_REQUIRED=frozenset(MarketEventType)-frozenset((MarketEventType.HEARTBEAT,MarketEventType.CLOCK_SYNC))
def validate_event(event):
 if not isinstance(event,MarketEvent):raise ValueError("MarketEvent is required")
 if not isinstance(event.sequence,int) or isinstance(event.sequence,bool) or event.sequence<0:raise ValueError("sequence must be a nonnegative integer")
 _aware(event.timestamp,"event timestamp")
 if not isinstance(event.source,str) or not event.source.strip():raise ValueError("event source is required")
 if not isinstance(event.event_type,MarketEventType):raise ValueError("event type is unsupported")
 if event.event_type in SYMBOL_REQUIRED and (not isinstance(event.symbol,str) or not SYMBOL.fullmatch(event.symbol)):raise ValueError("event symbol is invalid")
 if event.symbol is not None and (not isinstance(event.symbol,str) or not SYMBOL.fullmatch(event.symbol)):raise ValueError("event symbol is invalid")
 if not isinstance(event.payload,PAYLOADS[event.event_type]):raise ValueError("payload does not match event type")
 _validate_payload(event.payload);return event
def _validate_payload(payload):
 if isinstance(payload,QuotePayload):
  _positive(payload.bid,"bid");_positive(payload.ask,"ask");_nonnegative(payload.bid_size,"bid_size");_nonnegative(payload.ask_size,"ask_size")
  if payload.bid>payload.ask:raise ValueError("bid must not exceed ask")
 elif isinstance(payload,TradePayload):_positive(payload.price,"price");_nonnegative(payload.size,"size");_text(payload.trade_id,"trade_id")
 elif isinstance(payload,OrderBookSnapshotPayload):
  for level in (*payload.bids,*payload.asks):_level(level)
  if payload.bids and payload.asks and max(x.price for x in payload.bids)>min(x.price for x in payload.asks):raise ValueError("book bid must not exceed ask")
 elif isinstance(payload,OrderBookDeltaPayload):
  if payload.side not in ("BID","ASK") or payload.operation not in ("ADD","UPDATE","DELETE"):raise ValueError("book delta is invalid")
  _positive(payload.price,"price");_nonnegative(payload.size,"size")
 elif isinstance(payload,SymbolMetadataPayload):_text(payload.exchange,"exchange");_text(payload.currency,"currency");_positive(payload.tick_size,"tick_size")
 elif isinstance(payload,CorporateActionPayload):
  if not isinstance(payload.action_type,CorporateActionType):raise ValueError("corporate action type is invalid")
  _aware(payload.effective_timestamp,"effective timestamp")
  if payload.ratio is not None:_positive(payload.ratio,"ratio")
  if payload.cash_amount is not None:_nonnegative(payload.cash_amount,"cash_amount")
  if payload.new_symbol is not None and not SYMBOL.fullmatch(payload.new_symbol):raise ValueError("new symbol is invalid")
  if payload.action_type in (CorporateActionType.SPLIT,CorporateActionType.REVERSE_SPLIT) and payload.ratio is None:raise ValueError("split actions require a ratio")
  if payload.action_type is CorporateActionType.DIVIDEND and payload.cash_amount is None:raise ValueError("dividend actions require a cash amount")
  if payload.action_type is CorporateActionType.SYMBOL_CHANGE and payload.new_symbol is None:raise ValueError("symbol changes require a new symbol")
 elif isinstance(payload,ClockSyncPayload):_aware(payload.exchange_timestamp,"exchange timestamp");_aware(payload.local_timestamp,"local timestamp")
 elif isinstance(payload,(MarketStatusPayload,TradingHaltPayload,ResumePayload,HeartbeatPayload)):_text(getattr(payload,next(iter(payload.__dataclass_fields__))),"payload")
 elif isinstance(payload,SessionChangePayload) and not isinstance(payload.session,MarketSession):raise ValueError("market session is invalid")
def _level(value):
 if not isinstance(value,BookLevel):raise ValueError("book level is invalid")
 _positive(value.price,"price");_nonnegative(value.size,"size")
def _positive(value,name):
 _decimal_value(value,name)
 if value<=0:raise ValueError(f"{name} must be positive")
def _nonnegative(value,name):
 _decimal_value(value,name)
 if value<0:raise ValueError(f"{name} must be nonnegative")
def _decimal_value(value,name):
 if not isinstance(value,Decimal) or not value.is_finite():raise ValueError(f"{name} must be a finite Decimal")
def _aware(value,name):
 if not isinstance(value,datetime) or value.tzinfo is None:raise ValueError(f"{name} must be timezone-aware")
def _text(value,name):
 if not isinstance(value,str) or not value.strip():raise ValueError(f"{name} is required")
