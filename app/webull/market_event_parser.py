"""Classify and normalize Webull streaming messages into Atlas events."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import gzip
import json
import zlib

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload, TradePayload
from app.webull.errors import SerializationError


PayloadDecoder = Callable[[object], Mapping[str, object] | None]
Clock = Callable[[], datetime]


class StreamMessageClass(StrEnum):
    QUOTE = "QUOTE"
    TRADE = "TRADE"
    SNAPSHOT = "SNAPSHOT"
    HEARTBEAT = "HEARTBEAT"
    ACKNOWLEDGEMENT = "ACKNOWLEDGEMENT"
    CONTROL = "CONTROL"
    UNKNOWN = "UNKNOWN"


def _topic_name(topic: object) -> str:
    if isinstance(topic, bytes):
        try:
            topic = topic.decode("ascii")
        except UnicodeDecodeError:
            return ""
    return str(topic).strip().lower().rsplit("/", 1)[-1]


def classify_stream_topic(topic: object) -> StreamMessageClass:
    name = _topic_name(topic)
    if name in {"quote", "event-quote", "depth"}:
        return StreamMessageClass.QUOTE
    if name in {"tick", "event-tick", "trade"}:
        return StreamMessageClass.TRADE
    if name in {"snapshot", "event-snapshot"}:
        return StreamMessageClass.SNAPSHOT
    if name in {"echo", "heartbeat", "ping", "pong"}:
        return StreamMessageClass.HEARTBEAT
    if name in {"ack", "acknowledgement", "subscribe-ack", "suback"}:
        return StreamMessageClass.ACKNOWLEDGEMENT
    if name in {"notice", "control", "metadata"}:
        return StreamMessageClass.CONTROL
    return StreamMessageClass.UNKNOWN


def payload_metadata(payload: object) -> dict[str, object]:
    """Return an allow-listed description; never stringify payload contents."""

    topic: object = ""
    value = payload
    if isinstance(payload, tuple) and len(payload) == 2:
        topic, value = payload
    elif hasattr(payload, "topic") and hasattr(payload, "payload"):
        topic, value = getattr(payload, "topic"), getattr(payload, "payload")
    raw = bytes(value) if isinstance(value, (bytes, bytearray, memoryview)) else None
    result: dict[str, object] = {
        "topic": _topic_name(topic) or "unknown",
        "payload_type": type(value).__name__,
        "payload_length": len(raw) if raw is not None else None,
        "message_classification": classify_stream_topic(topic).value,
    }
    if raw is not None:
        from hashlib import sha256
        result["payload_hash"] = sha256(raw).hexdigest()[:12]
    return result


def _decompress(payload: bytes) -> tuple[bytes, str]:
    if payload.startswith(b"\x1f\x8b"):
        try:
            return gzip.decompress(payload), "gzip"
        except (OSError, EOFError) as exc:
            raise SerializationError("invalid gzip Webull stream payload") from exc
    if len(payload) >= 2 and payload[0] == 0x78:
        try:
            return zlib.decompress(payload), "zlib"
        except zlib.error:
            pass
    return payload, "none"


def _decode_protobuf(topic: object, payload: bytes) -> object:
    classification = classify_stream_topic(topic)
    try:
        if classification is StreamMessageClass.QUOTE:
            from webull.data.quotes.subscribe.quote_decoder import QuoteDecoder
            return QuoteDecoder().parse(payload)
        if classification is StreamMessageClass.TRADE:
            from webull.data.quotes.subscribe.tick_decoder import TickDecoder
            return TickDecoder().parse(payload)
        if classification is StreamMessageClass.SNAPSHOT:
            from webull.data.quotes.subscribe.snapshot_decoder import SnapshotDecoder
            return SnapshotDecoder().parse(payload)
    except Exception as exc:
        raise SerializationError(
            f"Webull {_topic_name(topic) or 'unknown'} protobuf could not be decoded"
        ) from exc
    raise SerializationError("raw Webull protobuf requires a supported topic")


def decode_json_payload(payload: object) -> Mapping[str, object] | None:
    """Route SDK objects, raw protobuf, JSON, and control messages explicitly."""

    if isinstance(payload, tuple) and len(payload) == 2:
        topic, value = payload
        classification = classify_stream_topic(topic)
        if classification in {
            StreamMessageClass.HEARTBEAT,
            StreamMessageClass.ACKNOWLEDGEMENT,
            StreamMessageClass.CONTROL,
            StreamMessageClass.UNKNOWN,
        }:
            return None
        if isinstance(value, (bytes, bytearray, memoryview)):
            raw, _compression = _decompress(bytes(value))
            value = _decode_protobuf(topic, raw)
        return _decode_sdk_result(topic, value)
    if hasattr(payload, "topic") and hasattr(payload, "payload"):
        return decode_json_payload((getattr(payload, "topic"), getattr(payload, "payload")))
    if isinstance(payload, Mapping):
        return payload
    if isinstance(payload, (bytearray, memoryview)):
        payload = bytes(payload)
    if isinstance(payload, bytes):
        payload, _compression = _decompress(payload)
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SerializationError(
                "raw Webull protobuf requires its MQTT topic"
            ) from exc
    if isinstance(payload, str):
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SerializationError("invalid Webull stream JSON") from exc
        if isinstance(value, Mapping):
            return value
    raise SerializationError("Webull stream payload must decode to an object")


def _decode_sdk_result(topic: object, value: object) -> Mapping[str, object]:
    """Normalize official SDK 2.0.14 QuoteResult/SnapshotResult/TickResult."""

    classification = classify_stream_topic(topic)
    basic = getattr(value, "basic", None)
    symbol = getattr(basic, "symbol", None)
    timestamp = getattr(basic, "timestamp", None)
    if not symbol:
        raise SerializationError("Webull SDK result is missing symbol")

    if classification is StreamMessageClass.QUOTE:
        bids = getattr(value, "bids", ())
        asks = getattr(value, "asks", ())
        if not bids or not asks:
            raise SerializationError("Webull SDK quote has no bid/ask")
        bid, ask = bids[0], asks[0]
        return {
            "event_type": "QUOTE", "symbol": symbol, "timestamp": timestamp,
            "bid": getattr(bid, "price", None), "ask": getattr(ask, "price", None),
            "bid_size": getattr(bid, "size", None), "ask_size": getattr(ask, "size", None),
        }
    if classification is StreamMessageClass.TRADE:
        tick_time = getattr(value, "time", None)
        # SDK 2.0.14 preserves Tick.time exactly as protobuf string data.
        # Webull emits either epoch milliseconds or an exchange-local HH:MM:SS.
        # Basic.timestamp is the unambiguous UTC instant for time-only values.
        if isinstance(tick_time, str) and tick_time.isdecimal():
            tick_time = int(tick_time)
        elif (
            isinstance(tick_time, str)
            and len(tick_time) == 8
            and tick_time[2] == ":"
            and tick_time[5] == ":"
        ):
            tick_time = timestamp
        return {
            "event_type": "TICK", "symbol": symbol,
            "timestamp": tick_time or timestamp,
            "last_price": getattr(value, "price", None),
            "size": getattr(value, "volume", None),
        }
    if classification is StreamMessageClass.SNAPSHOT:
        return {
            "event_type": "TICK", "symbol": symbol,
            "timestamp": getattr(value, "last_trade_time", None) or timestamp,
            "last_price": getattr(value, "price", None),
            "size": getattr(value, "volume", None), "trade_id": "snapshot",
        }
    raise SerializationError("unsupported Webull SDK streaming topic")


def _first(message: Mapping[str, object], *names: str) -> object | None:
    return next((message[name] for name in names if message.get(name) is not None), None)


def _decimal(message: Mapping[str, object], *names: str) -> Decimal:
    value = _first(message, *names)
    if value is None or isinstance(value, bool):
        raise SerializationError(f"missing numeric Webull field: {names[0]}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SerializationError(f"invalid numeric Webull field: {names[0]}") from exc
    if not result.is_finite() or result < 0:
        raise SerializationError(f"invalid numeric Webull field: {names[0]}")
    return result


def _timestamp(value: object | None, clock: Clock) -> datetime:
    if value is None:
        result = clock()
    elif isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        result = datetime.fromtimestamp(numeric, tz=UTC)
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SerializationError("invalid Webull stream timestamp") from exc
    else:
        raise SerializationError("invalid Webull stream timestamp")
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


class WebullMarketEventParser:
    def __init__(self, *, decoder: PayloadDecoder = decode_json_payload,
                 clock: Clock = lambda: datetime.now(UTC), source: str = "webull") -> None:
        if not source.strip():
            raise ValueError("source must not be blank")
        self._decoder, self._clock, self._source, self._next_sequence = decoder, clock, source, 1

    def __call__(self, payload: object) -> MarketEvent | None:
        message = self._decoder(payload)
        if message is None:
            return None
        event_name = str(_first(message, "event_type", "type", "sub_type") or "").upper()
        symbol = str(_first(message, "symbol", "ticker") or "").strip().upper()
        if not symbol:
            raise SerializationError("Webull stream message is missing symbol")
        sequence_value = _first(message, "sequence", "seq", "serial_no")
        try:
            sequence = self._next_sequence if sequence_value is None else int(sequence_value)
        except (TypeError, ValueError) as exc:
            raise SerializationError("invalid Webull stream sequence") from exc
        if sequence <= 0:
            raise SerializationError("invalid Webull stream sequence")
        self._next_sequence = max(self._next_sequence, sequence + 1)
        timestamp = _timestamp(_first(message, "timestamp", "time", "ts"), self._clock)
        if event_name in {"QUOTE", "BASIC", "BASIC_QUOTE"} or (
            _first(message, "bid", "bid_price") is not None and
            _first(message, "ask", "ask_price") is not None
        ):
            event_type = MarketEventType.QUOTE
            normalized = QuotePayload(
                _decimal(message, "bid", "bid_price"), _decimal(message, "ask", "ask_price"),
                _decimal(message, "bid_size", "bid_volume", "bid_qty"),
                _decimal(message, "ask_size", "ask_volume", "ask_qty"),
            )
        elif event_name in {"TRADE", "TICK", "DEAL", "SNAPSHOT"} or _first(message, "trade_price", "last_price") is not None:
            event_type = MarketEventType.TRADE
            normalized = TradePayload(
                _decimal(message, "trade_price", "last_price", "price"),
                _decimal(message, "trade_size", "size", "volume", "qty"),
                str(_first(message, "trade_id", "id", "serial_no") or sequence),
            )
        else:
            raise SerializationError(f"unsupported Webull stream event type: {event_name or 'unknown'}")
        return MarketEvent(sequence, timestamp, symbol, self._source, event_type, normalized)


__all__ = [
    "StreamMessageClass", "WebullMarketEventParser", "classify_stream_topic",
    "decode_json_payload", "payload_metadata",
]
