from __future__ import annotations
import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from app.market_data.events import append_event
from app.market_data.models import *

SCHEMA_VERSION = 1


def record_event(log: MarketEventLog, event: MarketEvent) -> MarketEventLog:
    return append_event(log, event)


def event_log_to_json(log: MarketEventLog) -> str:
    if log.schema_version != SCHEMA_VERSION: raise ValueError("unsupported market event schema")
    return json.dumps(_safe(log), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_log_from_json(payload: str) -> MarketEventLog:
    try:
        value = json.loads(payload)
        version = int(value.get("schema_version", 1))
        if version != SCHEMA_VERSION: raise ValueError("unsupported market event schema")
        log = MarketEventLog(schema_version=version)
        for raw in value.get("events", ()):
            event_type = MarketEventType(raw["event_type"])
            event = MarketEvent(int(raw["sequence"]), _dt(raw["timestamp"]), raw.get("symbol"),
                                raw["source"], event_type, _payload(event_type, raw["payload"]))
            log = append_event(log, event)
        return log
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("market event log JSON is malformed") from exc


def _payload(kind, value):
    if kind is MarketEventType.QUOTE: return QuotePayload(*(_decimal(value[key]) for key in ("bid", "ask", "bid_size", "ask_size")))
    if kind is MarketEventType.TRADE: return TradePayload(_decimal(value["price"]), _decimal(value["size"]), value["trade_id"])
    if kind is MarketEventType.BOOK_SNAPSHOT:
        levels = lambda name: tuple(BookLevel(_decimal(item["price"]), _decimal(item["size"])) for item in value[name])
        return OrderBookSnapshotPayload(levels("bids"), levels("asks"))
    if kind is MarketEventType.BOOK_DELTA: return OrderBookDeltaPayload(value["side"], _decimal(value["price"]), _decimal(value["size"]), value["operation"])
    if kind is MarketEventType.MARKET_STATUS: return MarketStatusPayload(value["status"])
    if kind is MarketEventType.TRADING_HALT: return TradingHaltPayload(value["reason"])
    if kind is MarketEventType.RESUME: return ResumePayload(value["reason"])
    if kind is MarketEventType.SYMBOL_METADATA: return SymbolMetadataPayload(value["exchange"], value["currency"], _decimal(value["tick_size"]))
    if kind is MarketEventType.CORPORATE_ACTION:
        return CorporateActionPayload(CorporateActionType(value["action_type"]), _dt(value["effective_timestamp"]),
                                      _optional(value.get("ratio")), _optional(value.get("cash_amount")), value.get("new_symbol"))
    if kind is MarketEventType.SESSION_CHANGE: return SessionChangePayload(MarketSession(value["session"]))
    if kind is MarketEventType.HEARTBEAT: return HeartbeatPayload(value["connection_id"])
    if kind is MarketEventType.CLOCK_SYNC: return ClockSyncPayload(_dt(value["exchange_timestamp"]), _dt(value["local_timestamp"]))
    raise ValueError("unsupported event type")


def _safe(value):
    if isinstance(value, Decimal): return format(value, "f")
    if isinstance(value, datetime): return value.isoformat()
    if isinstance(value, Enum): return value.value
    if is_dataclass(value) and not isinstance(value, type): return {field.name: _safe(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (tuple, list)): return [_safe(item) for item in value]
    return value
def _dt(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None: raise ValueError("event timestamps must be timezone-aware")
    return result
def _decimal(value): return Decimal(value)
def _optional(value): return None if value is None else Decimal(value)
