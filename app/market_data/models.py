from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class MarketEventType(StrEnum):
    QUOTE = "QUOTE"; TRADE = "TRADE"; BOOK_SNAPSHOT = "BOOK_SNAPSHOT"; BOOK_DELTA = "BOOK_DELTA"
    MARKET_STATUS = "MARKET_STATUS"; TRADING_HALT = "TRADING_HALT"; RESUME = "RESUME"
    SYMBOL_METADATA = "SYMBOL_METADATA"; CORPORATE_ACTION = "CORPORATE_ACTION"
    SESSION_CHANGE = "SESSION_CHANGE"; HEARTBEAT = "HEARTBEAT"; CLOCK_SYNC = "CLOCK_SYNC"


class MarketSession(StrEnum):
    PRE_MARKET = "PRE_MARKET"; REGULAR = "REGULAR"; AFTER_HOURS = "AFTER_HOURS"
    CLOSED = "CLOSED"; HOLIDAY = "HOLIDAY"; HALTED = "HALTED"


class CorporateActionType(StrEnum):
    SPLIT = "SPLIT"; REVERSE_SPLIT = "REVERSE_SPLIT"; DIVIDEND = "DIVIDEND"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"; MERGER = "MERGER"; DELISTING = "DELISTING"


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True, slots=True)
class QuotePayload: bid: Decimal; ask: Decimal; bid_size: Decimal; ask_size: Decimal
@dataclass(frozen=True, slots=True)
class TradePayload: price: Decimal; size: Decimal; trade_id: str
@dataclass(frozen=True, slots=True)
class OrderBookSnapshotPayload: bids: tuple[BookLevel, ...]; asks: tuple[BookLevel, ...]
@dataclass(frozen=True, slots=True)
class OrderBookDeltaPayload: side: str; price: Decimal; size: Decimal; operation: str
@dataclass(frozen=True, slots=True)
class MarketStatusPayload: status: str
@dataclass(frozen=True, slots=True)
class TradingHaltPayload: reason: str
@dataclass(frozen=True, slots=True)
class ResumePayload: reason: str
@dataclass(frozen=True, slots=True)
class SymbolMetadataPayload: exchange: str; currency: str; tick_size: Decimal
@dataclass(frozen=True, slots=True)
class CorporateActionPayload:
    action_type: CorporateActionType
    effective_timestamp: datetime
    ratio: Decimal | None = None
    cash_amount: Decimal | None = None
    new_symbol: str | None = None
@dataclass(frozen=True, slots=True)
class SessionChangePayload: session: MarketSession
@dataclass(frozen=True, slots=True)
class HeartbeatPayload: connection_id: str
@dataclass(frozen=True, slots=True)
class ClockSyncPayload: exchange_timestamp: datetime; local_timestamp: datetime

EventPayload = (QuotePayload | TradePayload | OrderBookSnapshotPayload | OrderBookDeltaPayload |
                MarketStatusPayload | TradingHaltPayload | ResumePayload | SymbolMetadataPayload |
                CorporateActionPayload | SessionChangePayload | HeartbeatPayload | ClockSyncPayload)


@dataclass(frozen=True, slots=True)
class MarketEvent:
    sequence: int
    timestamp: datetime
    symbol: str | None
    source: str
    event_type: MarketEventType
    payload: EventPayload


@dataclass(frozen=True, slots=True)
class MarketEventLog:
    events: tuple[MarketEvent, ...] = ()
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class ClockMeasurement:
    exchange_timestamp: datetime
    local_timestamp: datetime
    latency_microseconds: int
    clock_skew_microseconds: int

