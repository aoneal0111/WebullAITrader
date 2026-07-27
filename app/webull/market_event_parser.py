"""Normalize decoded Webull streaming messages into Atlas market events."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
from typing import Any

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload, TradePayload
from app.webull.errors import SerializationError


PayloadDecoder = Callable[[object], Mapping[str, object]]
Clock = Callable[[], datetime]


def decode_json_payload(payload: object) -> Mapping[str, object]:
    """Decode dictionary or UTF-8 JSON payloads.

    The official SDK may expose protobuf bytes for some subscriptions. Those
    subscriptions must provide a dedicated decoder instead of silently guessing
    a schema here.
    """

    if isinstance(payload, Mapping):
        return payload
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SerializationError("Webull stream payload is not UTF-8 JSON") from exc
    if isinstance(payload, str):
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SerializationError("invalid Webull stream JSON") from exc
        if isinstance(value, Mapping):
            return value
    raise SerializationError("Webull stream payload must decode to an object")


def _first(message: Mapping[str, object], *names: str) -> object | None:
    for name in names:
        if name in message and message[name] is not None:
            return message[name]
    return None


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
    """Convert decoded quote/trade messages into immutable Atlas events."""

    def __init__(
        self,
        *,
        decoder: PayloadDecoder = decode_json_payload,
        clock: Clock = lambda: datetime.now(UTC),
        source: str = "webull",
    ) -> None:
        if not source.strip():
            raise ValueError("source must not be blank")
        self._decoder = decoder
        self._clock = clock
        self._source = source
        self._next_sequence = 1

    def __call__(self, payload: object) -> MarketEvent:
        message = self._decoder(payload)
        event_name = str(_first(message, "event_type", "type", "sub_type") or "").upper()
        symbol = str(_first(message, "symbol", "ticker") or "").strip().upper()
        if not symbol:
            raise SerializationError("Webull stream message is missing symbol")

        sequence_value = _first(message, "sequence", "seq", "serial_no")
        if sequence_value is None:
            sequence = self._next_sequence
        else:
            try:
                sequence = int(sequence_value)
            except (TypeError, ValueError) as exc:
                raise SerializationError("invalid Webull stream sequence") from exc
            if sequence <= 0:
                raise SerializationError("invalid Webull stream sequence")
        self._next_sequence = max(self._next_sequence, sequence + 1)
        timestamp = _timestamp(_first(message, "timestamp", "time", "ts"), self._clock)

        if event_name in {"QUOTE", "BASIC", "BASIC_QUOTE", "SNAPSHOT"} or (
            _first(message, "bid", "bid_price") is not None
            and _first(message, "ask", "ask_price") is not None
        ):
            event_type = MarketEventType.QUOTE
            normalized_payload = QuotePayload(
                bid=_decimal(message, "bid", "bid_price"),
                ask=_decimal(message, "ask", "ask_price"),
                bid_size=_decimal(message, "bid_size", "bid_volume", "bid_qty"),
                ask_size=_decimal(message, "ask_size", "ask_volume", "ask_qty"),
            )
        elif event_name in {"TRADE", "TICK", "DEAL"} or _first(message, "trade_price", "last_price") is not None:
            event_type = MarketEventType.TRADE
            trade_id = str(_first(message, "trade_id", "id", "serial_no") or sequence)
            normalized_payload = TradePayload(
                price=_decimal(message, "trade_price", "last_price", "price"),
                size=_decimal(message, "trade_size", "size", "volume", "qty"),
                trade_id=trade_id,
            )
        else:
            raise SerializationError(f"unsupported Webull stream event type: {event_name or 'unknown'}")

        return MarketEvent(
            sequence=sequence,
            timestamp=timestamp,
            symbol=symbol,
            source=self._source,
            event_type=event_type,
            payload=normalized_payload,
        )


__all__ = ["WebullMarketEventParser", "decode_json_payload"]
