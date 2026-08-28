"""Classify and normalize Webull streaming messages into Atlas events."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import gzip
from hashlib import sha256
import json
import re
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
        result["payload_hash"] = sha256(raw).hexdigest()[:12]
    return result


def decoder_failure_metadata(
    payload: object,
    error: SerializationError,
) -> dict[str, object]:
    """Describe a decode failure using a strict, payload-free allow-list."""

    metadata = payload_metadata(payload)
    topic: object = ""
    value = payload
    if isinstance(payload, tuple) and len(payload) == 2:
        topic, value = payload
    elif hasattr(payload, "topic") and hasattr(payload, "payload"):
        topic, value = getattr(payload, "topic"), getattr(payload, "payload")
    classification = classify_stream_topic(topic)
    basic = getattr(value, "basic", None)
    symbol = getattr(basic, "symbol", None)
    safe_symbol = (
        str(symbol).strip().upper()
        if symbol is not None
        and re.fullmatch(r"[A-Za-z0-9.\-]{1,24}", str(symbol).strip())
        else None
    )
    timestamp = (
        getattr(value, "time", None)
        if classification is StreamMessageClass.TRADE
        else getattr(value, "last_trade_time", None)
        if classification is StreamMessageClass.SNAPSHOT
        else getattr(basic, "timestamp", None)
    )
    price = (
        getattr(value, "price", None)
        if classification in {StreamMessageClass.TRADE, StreamMessageClass.SNAPSHOT}
        else _first_quote_field(value, "price")
    )
    volume = (
        getattr(value, "volume", None)
        if classification in {StreamMessageClass.TRADE, StreamMessageClass.SNAPSHOT}
        else _first_quote_field(value, "size")
    )
    result = {
        "topic": metadata["topic"],
        "sdk_object_type": type(value).__name__,
        "protobuf_result_type": type(value).__name__,
        "message_classification": classification.value,
        "symbol": safe_symbol,
        "failure_field": getattr(error, "diagnostic_field", "unknown"),
        "decoder_selected": {
            StreamMessageClass.QUOTE: "QuoteDecoder/QuoteResult",
            StreamMessageClass.TRADE: "TickDecoder/TickResult",
            StreamMessageClass.SNAPSHOT: "SnapshotDecoder/SnapshotResult",
        }.get(classification, "none"),
        "timestamp_field_type": type(timestamp).__name__,
        "price_field_type": type(price).__name__,
        "volume_field_type": type(volume).__name__,
        "payload_length": metadata["payload_length"],
        "payload_hash": metadata.get("payload_hash"),
        "error_stage": getattr(error, "diagnostic_stage", "unknown"),
    }
    return result


def _first_quote_field(value: object, field: str) -> object | None:
    for side_name in ("bids", "asks"):
        side = getattr(value, side_name, ())
        if side:
            return getattr(side[0], field, None)
    return None


def _serialization_error(
    message: str,
    *,
    field: str,
    stage: str,
) -> SerializationError:
    error = SerializationError(message)
    object.__setattr__(error, "diagnostic_field", field)
    object.__setattr__(error, "diagnostic_stage", stage)
    return error


def _decompress(payload: bytes) -> tuple[bytes, str]:
    if payload.startswith(b"\x1f\x8b"):
        try:
            return gzip.decompress(payload), "gzip"
        except (OSError, EOFError) as exc:
            raise _serialization_error(
                "invalid gzip Webull stream payload",
                field="payload", stage="decompression",
            ) from exc
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
        raise _serialization_error(
            f"Webull {_topic_name(topic) or 'unknown'} protobuf could not be decoded",
            field="payload", stage="protobuf_decode",
        ) from exc
    raise _serialization_error(
        "raw Webull protobuf requires a supported topic",
        field="topic", stage="decoder_selection",
    )


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
            raise _serialization_error(
                "raw Webull protobuf requires its MQTT topic",
                field="topic", stage="decoder_selection",
            ) from exc
    if isinstance(payload, str):
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise _serialization_error(
                "invalid Webull stream JSON",
                field="payload", stage="json_decode",
            ) from exc
        if isinstance(value, Mapping):
            return value
    raise _serialization_error(
        "Webull stream payload must decode to an object",
        field="payload", stage="payload_decode",
    )


def _decode_sdk_result(topic: object, value: object) -> Mapping[str, object] | None:
    """Normalize official SDK 2.0.14 QuoteResult/SnapshotResult/TickResult."""

    classification = classify_stream_topic(topic)
    basic = getattr(value, "basic", None)
    symbol = getattr(basic, "symbol", None)
    timestamp = getattr(basic, "timestamp", None)
    if not symbol:
        raise _serialization_error(
            "Webull SDK result is missing symbol", field="symbol", stage="sdk_normalize"
        )

    if classification is StreamMessageClass.QUOTE:
        bids = getattr(value, "bids", ())
        asks = getattr(value, "asks", ())
        if not bids or not asks:
            return None
        bid, ask = bids[0], asks[0]
        if any(
            item is None
            for item in (
                getattr(bid, "price", None), getattr(ask, "price", None),
                getattr(bid, "size", None), getattr(ask, "size", None),
            )
        ):
            return None
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
        price = getattr(value, "price", None)
        volume = getattr(value, "volume", None)
        if price is None or volume is None:
            return None
        return {
            "event_type": "TICK", "symbol": symbol,
            "timestamp": tick_time or timestamp,
            "last_price": price, "size": volume,
        }
    if classification is StreamMessageClass.SNAPSHOT:
        price = getattr(value, "price", None)
        volume = getattr(value, "volume", None)
        # SnapshotResult is a sparse update container in SDK 2.0.14. A
        # symbol-only or non-volume update is valid transport traffic, but it
        # is not a normalized market event and must not degrade decoder health.
        if volume is None:
            return None
        return {
            "event_type": "SNAPSHOT", "symbol": symbol,
            "timestamp": getattr(value, "last_trade_time", None) or timestamp,
            "last_price": price,
            "size": volume, "trade_id": "snapshot",
        }
    raise _serialization_error(
        "unsupported Webull SDK streaming topic",
        field="topic", stage="decoder_selection",
    )


def _first(message: Mapping[str, object], *names: str) -> object | None:
    return next((message[name] for name in names if message.get(name) is not None), None)


def _decimal(message: Mapping[str, object], *names: str) -> Decimal:
    value = _first(message, *names)
    if value is None or isinstance(value, bool):
        raise _serialization_error(
            f"missing numeric Webull field: {names[0]}",
            field=names[0], stage="event_normalize",
        )
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise _serialization_error(
            f"invalid numeric Webull field: {names[0]}",
            field=names[0], stage="event_normalize",
        ) from exc
    if not result.is_finite() or result < 0:
        raise _serialization_error(
            f"invalid numeric Webull field: {names[0]}",
            field=names[0], stage="event_normalize",
        )
    return result


def _timestamp(value: object | None, clock: Clock) -> datetime:
    if value is None:
        result = clock()
    elif isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        result = _epoch_timestamp(Decimal(str(value)))
    elif isinstance(value, str):
        normalized = value.strip()
        try:
            result = (
                _epoch_timestamp(Decimal(normalized))
                if normalized.replace(".", "", 1).isdecimal()
                else datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            )
        except (InvalidOperation, ValueError, OverflowError, OSError) as exc:
            raise _serialization_error(
                "invalid Webull stream timestamp",
                field="timestamp", stage="timestamp_normalize",
            ) from exc
    else:
        raise _serialization_error(
            "invalid Webull stream timestamp",
            field="timestamp", stage="timestamp_normalize",
        )
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _epoch_timestamp(value: Decimal) -> datetime:
    magnitude = abs(value)
    divisor = (
        Decimal("1000000000") if magnitude >= Decimal("100000000000000000")
        else Decimal("1000000") if magnitude >= Decimal("100000000000000")
        else Decimal("1000") if magnitude >= Decimal("100000000000")
        else Decimal("1")
    )
    return datetime.fromtimestamp(float(value / divisor), tz=UTC)


class WebullMarketEventParser:
    def __init__(self, *, decoder: PayloadDecoder = decode_json_payload,
                 clock: Clock = lambda: datetime.now(UTC), source: str = "webull") -> None:
        if not source.strip():
            raise ValueError("source must not be blank")
        self._decoder, self._clock, self._source, self._next_sequence = decoder, clock, source, 1
        self._last_trade_price: dict[str, object] = {}

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
            price = _first(message, "trade_price", "last_price", "price")
            retained_snapshot_price = False
            if event_name == "SNAPSHOT" and price is None:
                price = self._last_trade_price.get(symbol)
                if price is None:
                    return None
                retained_snapshot_price = True
            normalized = TradePayload(
                _decimal({**message, "trade_price": price}, "trade_price"),
                _decimal(message, "trade_size", "size", "volume", "qty"),
                (
                    "snapshot-retained-price"
                    if retained_snapshot_price
                    else str(_first(message, "trade_id", "id", "serial_no") or sequence)
                ),
            )
        else:
            raise SerializationError(f"unsupported Webull stream event type: {event_name or 'unknown'}")
        if event_type is MarketEventType.TRADE:
            self._last_trade_price[symbol] = normalized.price
        return MarketEvent(sequence, timestamp, symbol, self._source, event_type, normalized)


__all__ = [
    "StreamMessageClass", "WebullMarketEventParser", "classify_stream_topic",
    "decode_json_payload", "decoder_failure_metadata", "payload_metadata",
]
