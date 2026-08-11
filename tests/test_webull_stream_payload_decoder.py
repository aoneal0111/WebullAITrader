from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import gzip
from types import SimpleNamespace

import pytest

from app.market_data.models import MarketEventType
from app.webull.configuration import ReconnectPolicy
from app.webull.errors import SerializationError
from app.webull.market_event_parser import (
    StreamMessageClass,
    WebullMarketEventParser,
    classify_stream_topic,
    payload_metadata,
)
from app.webull.websocket_client import WebullWebSocketClient


NOW = datetime(2026, 8, 6, 15, 30, tzinfo=UTC)


class Logger:
    def __init__(self) -> None:
        self.records: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def log(self, *args: object, **fields: object) -> None:
        self.records.append((args, fields))


class Backend:
    def __init__(self, messages: list[object]) -> None:
        self.messages = list(messages)
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def subscribe(self, channels: tuple[str, ...]) -> None:
        self.channels = channels

    def receive(self) -> object | None:
        return self.messages.pop(0) if self.messages else None


def client(messages: list[object], *, threshold: int = 5):
    logger = Logger()
    lifecycle: list[tuple[object, ...]] = []
    result = WebullWebSocketClient(
        Backend(messages),
        WebullMarketEventParser(clock=lambda: NOW),
        ReconnectPolicy(maximum_attempts=0, backoff_seconds=Decimal("0")),
        lambda seconds: None,
        logger,
        lifecycle_sink=lambda *items: lifecycle.append(items),
        consecutive_decode_failure_threshold=threshold,
    )
    result.connect()
    result.subscribe(("AAPL", "MSFT"))
    return result, logger, lifecycle


def sdk_quote(symbol: str = "AAPL") -> object:
    return SimpleNamespace(
        basic=SimpleNamespace(symbol=symbol, timestamp=1_786_030_200_000),
        bids=[SimpleNamespace(price=Decimal("199.90"), size=10)],
        asks=[SimpleNamespace(price=Decimal("200.10"), size=12)],
    )


def sdk_tick(symbol: str = "AAPL") -> object:
    return SimpleNamespace(
        basic=SimpleNamespace(symbol=symbol, timestamp=1_786_030_200_000),
        time=1_786_030_201_000,
        price=Decimal("200.05"),
        volume=3,
    )


def quote_protobuf(symbol: str = "AAPL") -> bytes:
    from webull.data.quotes.subscribe.message_pb2 import Quote

    message = Quote()
    message.basic.symbol = symbol
    message.basic.timestamp = "1786030200000"
    bid = message.bids.add()
    bid.price, bid.size = "199.90", "10"
    ask = message.asks.add()
    ask.price, ask.size = "200.10", "12"
    return message.SerializeToString()


def tick_protobuf(symbol: str = "AAPL") -> bytes:
    from webull.data.quotes.subscribe.message_pb2 import Tick

    message = Tick()
    message.basic.symbol = symbol
    message.basic.timestamp = "1786030200000"
    message.time = "1786030201000"
    message.price = "200.05"
    message.volume = "3"
    return message.SerializeToString()


def test_sdk_2014_already_decoded_quote_and_trade_route_by_exact_topic() -> None:
    parser = WebullMarketEventParser(clock=lambda: NOW)

    quote = parser(("quote", sdk_quote()))
    trade = parser(("tick", sdk_tick()))

    assert quote.event_type is MarketEventType.QUOTE
    assert quote.symbol == "AAPL"
    assert quote.payload.bid == Decimal("199.90")
    assert quote.payload.ask == Decimal("200.10")
    assert trade.event_type is MarketEventType.TRADE
    assert trade.symbol == "AAPL"
    assert trade.payload.price == Decimal("200.05")
    assert trade.timestamp.timestamp() == 1_786_030_201


def test_sdk_2014_tick_epoch_milliseconds_string_is_not_parsed_as_iso_date() -> None:
    tick = sdk_tick()
    tick.time = "1786041384332"

    event = WebullMarketEventParser(clock=lambda: NOW)(("tick", tick))

    assert event.event_type is MarketEventType.TRADE
    assert event.timestamp.timestamp() == 1_786_041_384.332
    assert event.payload.price == Decimal("200.05")
    assert event.payload.size == Decimal("3")


@pytest.mark.parametrize(
    "timestamp",
    ("1786041384.332", "1786041384332", "1786041384332000", "1786041384332000000"),
)
def test_parser_normalizes_epoch_seconds_milliseconds_microseconds_and_nanoseconds(
    timestamp: str,
) -> None:
    event = WebullMarketEventParser(clock=lambda: NOW)(
        {
            "event_type": "QUOTE", "symbol": "AAPL", "timestamp": timestamp,
            "bid": "199.90", "ask": "200.10", "bid_size": "10", "ask_size": "12",
        }
    )

    assert event.timestamp.timestamp() == pytest.approx(1_786_041_384.332)


def test_sdk_2014_tick_time_of_day_uses_unambiguous_basic_timestamp() -> None:
    tick = sdk_tick()
    tick.time = "12:58:19"
    tick.basic.timestamp = 1_786_041_499_154

    event = WebullMarketEventParser(clock=lambda: NOW)(("tick", tick))

    assert event.event_type is MarketEventType.TRADE
    assert event.timestamp.timestamp() == 1_786_041_499.154
    assert event.payload.price == Decimal("200.05")
    assert event.payload.size == Decimal("3")


def test_sdk_2014_tick_protobuf_schema_matches_live_field_types() -> None:
    from webull.data.quotes.subscribe.message_pb2 import Tick

    fields = {field.name: field.type for field in Tick.DESCRIPTOR.fields}

    assert fields == {
        "basic": 11,
        "time": 9,
        "price": 9,
        "volume": 9,
        "side": 9,
    }


def test_raw_quote_and_trade_protobuf_bytes_use_channel_specific_schemas() -> None:
    parser = WebullMarketEventParser(clock=lambda: NOW)

    quote = parser(("quote", quote_protobuf()))
    trade = parser(("tick", tick_protobuf()))
    compressed = parser(("quote", gzip.compress(quote_protobuf("MSFT"))))

    assert quote.event_type is MarketEventType.QUOTE
    assert trade.event_type is MarketEventType.TRADE
    assert compressed.symbol == "MSFT"


@pytest.mark.parametrize(
    ("topic", "classification"),
    (
        ("quote", StreamMessageClass.QUOTE),
        ("tick", StreamMessageClass.TRADE),
        ("snapshot", StreamMessageClass.SNAPSHOT),
        ("echo", StreamMessageClass.HEARTBEAT),
        ("subscribe-ack", StreamMessageClass.ACKNOWLEDGEMENT),
        ("notice", StreamMessageClass.CONTROL),
        ("future-channel", StreamMessageClass.UNKNOWN),
    ),
)
def test_topic_classification_and_non_market_messages_are_safe(topic, classification) -> None:
    assert classify_stream_topic(topic) is classification
    if classification in {
        StreamMessageClass.HEARTBEAT,
        StreamMessageClass.ACKNOWLEDGEMENT,
        StreamMessageClass.CONTROL,
        StreamMessageClass.UNKNOWN,
    }:
        assert WebullMarketEventParser(clock=lambda: NOW)((topic, b"ignored")) is None


def test_one_malformed_payload_is_isolated_and_success_restores_health() -> None:
    stream, _logger, lifecycle = client(
        [("quote", b"\xff"), ("tick", sdk_tick())]
    )

    event = stream.receive()

    assert event.event_type is MarketEventType.TRADE
    assert stream.consecutive_decode_failures == 0
    assert stream.decoder_health == "STREAM_CONNECTED"
    assert [item[0] for item in lifecycle] == ["parse_failed", "decode_recovered"]
    assert stream.backend.connected is True


def test_consecutive_decode_failure_threshold_fails_only_at_bound() -> None:
    stream, _logger, lifecycle = client(
        [("quote", b"\xff"), ("quote", b"\xff"), ("quote", b"\xff")],
        threshold=3,
    )

    with pytest.raises(SerializationError):
        stream.receive()

    assert stream.consecutive_decode_failures == 3
    assert stream.decoder_health == "STREAM_FAILED"
    assert [item[0] for item in lifecycle] == [
        "parse_failed", "decode_threshold_exceeded"
    ]
    assert stream.backend.connected is True


def test_unknown_topic_is_recorded_and_skipped_without_decode_failure() -> None:
    stream, logger, lifecycle = client([("new-metadata", b"opaque")])

    assert stream.receive() is None
    assert stream.decoder_health == "STREAM_PAYLOAD_UNSUPPORTED"
    assert stream.consecutive_decode_failures == 0
    assert lifecycle == []
    assert any(
        fields.get("message_classification") == "UNKNOWN"
        for _args, fields in logger.records
    )


def test_sanitized_diagnostics_never_include_raw_payload_or_secrets() -> None:
    raw = b"token=top-secret app_secret=never-log"
    metadata = payload_metadata(("quote", raw))
    stream, logger, _lifecycle = client(
        [("quote", raw), ("quote", quote_protobuf())]
    )
    stream.receive()
    rendered = repr((metadata, logger.records))

    assert metadata["payload_length"] == len(raw)
    assert len(str(metadata["payload_hash"])) == 12
    assert "top-secret" not in rendered
    assert "never-log" not in rendered
    assert repr(raw) not in rendered


def test_sparse_sdk_snapshot_is_valid_skipped_traffic_not_a_decode_failure() -> None:
    snapshot = SimpleNamespace(
        basic=SimpleNamespace(symbol="AAPL", timestamp=1_786_030_200_000),
        last_trade_time=None,
        price=None,
        volume=None,
    )
    stream, logger, lifecycle = client(
        [("snapshot", snapshot), ("tick", sdk_tick())]
    )

    assert stream.receive() is None
    event = stream.receive()

    assert event is not None and event.event_type is MarketEventType.TRADE
    assert stream.decoder_health == "STREAM_CONNECTED"
    assert stream.decoder_diagnostics["decode_failures_by_event_class"] == ()
    assert stream.decoder_diagnostics["skipped_payloads_by_event_class"] == (
        ("SNAPSHOT", 1),
    )
    assert lifecycle == []
    assert not any(args == ("stream_receive", "decode_failed") for args, _ in logger.records)


def test_sparse_sdk_snapshot_recovers_an_isolated_decode_failure() -> None:
    snapshot = SimpleNamespace(
        basic=SimpleNamespace(symbol="AAPL", timestamp=1_786_030_200_000),
        last_trade_time=None,
        price=None,
        volume=None,
    )
    stream, _logger, lifecycle = client([
        ("quote", b"\xff"),
        ("snapshot", snapshot),
    ])

    assert stream.receive() is None
    assert stream.decoder_health == "STREAM_CONNECTED"
    assert stream.consecutive_decode_failures == 0
    assert [item[0] for item in lifecycle] == ["parse_failed", "decode_recovered"]


def test_snapshot_volume_uses_last_valid_trade_price_when_price_is_sparse() -> None:
    parser = WebullMarketEventParser(clock=lambda: NOW)
    parser(("tick", sdk_tick()))
    snapshot = SimpleNamespace(
        basic=SimpleNamespace(symbol="AAPL", timestamp=1_786_030_202_000),
        last_trade_time=None,
        price=None,
        volume=Decimal("1900000"),
    )

    event = parser(("snapshot", snapshot))

    assert event is not None
    assert event.payload.trade_id == "snapshot"
    assert event.payload.price == Decimal("200.05")
    assert event.payload.size == Decimal("1900000")


def test_quote_trade_routing_remains_symbol_specific_across_subscription_set() -> None:
    stream, _logger, _lifecycle = client(
        [("quote", sdk_quote("AAPL")), ("tick", sdk_tick("MSFT"))]
    )

    quote = stream.receive()
    trade = stream.receive()

    assert (quote.symbol, quote.event_type) == ("AAPL", MarketEventType.QUOTE)
    assert (trade.symbol, trade.event_type) == ("MSFT", MarketEventType.TRADE)
    assert stream.backend.channels == ("AAPL", "MSFT")
