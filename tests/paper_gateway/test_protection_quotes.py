from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from app.market_data.models import MarketEventType
from app.paper_trading.protection_quote_check import observe, main
from app.webull.protection_quotes import ProtectionQuoteDecoder, ProtectionQuoteError

NOW = datetime(2026, 10, 15, 19, tzinfo=timezone.utc)


def envelope(symbol="SPY", *, milliseconds=None, size="12"):
    # Exercise the pinned SDK's actual protobuf -> QuoteResult representation.
    from webull.data.quotes.subscribe.message_pb2 import Quote
    from webull.data.quotes.subscribe.quote_result import QuoteResult
    pb = Quote()
    pb.basic.symbol = symbol
    pb.basic.timestamp = str(int(NOW.timestamp()*1000) if milliseconds is None else milliseconds)
    pb.bids.add(price="640.10", size=size)
    pb.asks.add(price="640.12", size="8")
    return SimpleNamespace(topic="quote", payload=QuoteResult(pb), received_timestamp=NOW,
                           generation=7)


def decoder():
    return ProtectionQuoteDecoder(("SPY", "QQQ"), 7, clock=lambda: NOW + timedelta(milliseconds=200))


def test_real_sdk_quote_preserves_provider_and_receipt_times_and_sizes():
    incoming = envelope()
    assert incoming.payload.basic.get_timestamp_as_utc().tzinfo is None
    event = decoder().decode(incoming)
    assert event.event_type is MarketEventType.QUOTE
    assert event.timestamp == NOW and event.received_timestamp == NOW
    assert event.dequeued_timestamp == NOW + timedelta(milliseconds=200)
    assert (event.payload.bid_size, event.payload.ask_size) == (Decimal(12), Decimal(8))
    assert event.payload.bids[0].price == Decimal("640.10")


@pytest.mark.parametrize("topic", ["snapshot", "tick", "notice", "echo", "event-quote", "RESUME", "HALT"])
def test_non_quotes_never_become_status_or_quote_authority(topic):
    row = envelope()
    row.topic = topic
    assert decoder().decode(row) is None


@pytest.mark.parametrize("bad", ["missing_time", "naive_receipt", "missing_receipt", "stale", "future",
                                "seconds", "generation", "boolean_generation", "symbol", "empty_book",
                                "crossed", "missing_size", "negative_size", "nan", "oversized", "depth"])
def test_invalid_quote_is_rejected_with_fixed_diagnostic(bad):
    row = envelope()
    if bad == "missing_time": row.payload.basic.timestamp = None
    if bad == "naive_receipt": row.received_timestamp = NOW.replace(tzinfo=None)
    if bad == "missing_receipt": row.received_timestamp = None
    if bad == "stale": row.payload.basic.timestamp -= 6000
    if bad == "future": row.payload.basic.timestamp += 1000
    if bad == "seconds": row.payload.basic.timestamp //= 1000
    if bad == "generation": row.generation = 6
    if bad == "boolean_generation": row.generation = True
    if bad == "symbol": row.payload.basic.symbol = "UNREQUESTED_PRIVATE_VALUE"
    if bad == "empty_book": row.payload.bids.clear()
    if bad == "crossed": row.payload.bids[0].price = Decimal(999)
    if bad == "missing_size": row.payload.bids[0].size = ""
    if bad == "negative_size": row.payload.bids[0].size = "-1"
    if bad == "nan": row.payload.bids[0].size = "NaN"
    if bad == "oversized": row.payload.bids[0].size = "9"*65
    if bad == "depth": row.payload.bids *= 11
    with pytest.raises(ProtectionQuoteError) as error:
        decoder().decode(row)
    assert "PRIVATE" not in str(error.value)


def test_zero_provider_size_is_retained_without_fabricated_liquidity():
    assert decoder().decode(envelope(size="0")).payload.bid_size == 0


class Backend:
    def __init__(self, rows):
        self.rows = deque(rows)
        self.registration_ready = True
        self.generation_metrics = {"generation": 7}
        self.calls = []
    def connect(self): self.calls.append("connect")
    def subscribe(self, symbols): self.calls.append(("subscribe", symbols))
    def receive(self): return self.rows.popleft() if self.rows else None
    def disconnect(self): self.calls.append("disconnect")


def run(backend, **kwargs):
    ticks = iter(i/10 for i in range(1000))
    return observe(backend, ("SPY", "QQQ"), duration_seconds=1,
                   timer=lambda: next(ticks), clock=lambda: NOW + timedelta(milliseconds=200), **kwargs)


def test_probe_stops_after_bounded_symbol_coverage_and_disconnects():
    backend = Backend([envelope(), envelope("QQQ"), envelope()])
    report = run(backend)
    assert report["state"] == "QUOTE_FIELDS_OBSERVED"
    assert report["quotes"] == 2 and len(report["samples"]) == 2
    assert len(backend.rows) == 1
    assert backend.calls == ["connect", ("subscribe", ("SPY", "QQQ")), "disconnect"]
    assert report["status_authorization"] == "NONE" and not report["live_feed_validated"]
    assert report["orders"] == 0


def test_probe_limits_messages_and_reports_partial_coverage():
    backend = Backend([envelope()]*10)
    report = run(backend, maximum_messages=2)
    assert report["messages"] == 2 and report["state"] == "PARTIAL_QUOTE_COVERAGE"
    assert report["missing_symbols"] == ["QQQ"]
    assert backend.calls[-1] == "disconnect"


def test_probe_ignores_notice_without_status_authority():
    row = envelope()
    row.topic = "notice"
    report = run(Backend([row]))
    assert report["ignored"] == 1 and report["state"] == "NO_VALID_QUOTES"


@pytest.mark.parametrize("failure", ["connect", "subscribe", "receive", "disconnect"])
def test_transport_faults_are_sanitized_and_cleanup_attempted(failure):
    backend = Backend([envelope(), envelope("QQQ")])
    cleaned = []
    def failed(*args):
        raise RuntimeError("PRIVATE_CREDENTIAL_VALUES")
    def cleanup():
        cleaned.append(True)
        if failure == "disconnect": failed()
    setattr(backend, failure, failed)
    backend.disconnect = cleanup
    report = run(backend)
    assert cleaned == [True]
    assert "PRIVATE" not in json.dumps(report)
    assert report["state"] in {"STREAM_OR_DECODE_UNAVAILABLE", "STREAM_CLEANUP_FAILED"}


def test_offline_check_never_constructs_transport_or_loads_configuration(capsys, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline check touched configuration")
    monkeypatch.setattr("app.configuration.loader.load_configuration", forbidden)
    monkeypatch.setattr("app.webull.sdk_streaming_adapter.create_official_stream_backend", forbidden)
    assert main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["network_requests"] == report["orders"] == 0


def test_disconnected_stream_cannot_report_complete_quote_coverage():
    backend = Backend([])
    backend.registration_ready = False
    assert run(backend)["state"] == "REGISTRATION_UNAVAILABLE"
    assert backend.calls == ["connect", "disconnect"]


def test_decoded_quotes_preserve_status_gate_and_provider_fill_sizes(tmp_path):
    from app.market_data.models import MarketEvent, ResumePayload
    from app.paper_trading.protection_feed import PaperProtectionFeed
    from app.paper_trading.protection_host import PaperProtectionHost
    from app.paper_trading.protection_host_check import seed
    from app.webull.protection_quotes import SOURCE
    path = tmp_path / "paper.sqlite3"
    seed(path)
    wall = [NOW - timedelta(seconds=1)]
    host = PaperProtectionHost(path, environment="PAPER", clock=lambda: wall[0])
    feed = PaperProtectionFeed(host, source=SOURCE, silence_seconds=5)
    try:
        token = feed.connect()
        row = envelope("DEMO", size="3")
        row.payload.bids[0].price = Decimal("9.40")
        row.payload.asks[0].price = Decimal("9.41")
        wall[0] = NOW + timedelta(milliseconds=200)
        event = ProtectionQuoteDecoder(("DEMO",), 7, clock=lambda: wall[0]).decode(row)
        assert feed.process(token, event) == "HALTED"
        assert host._quantity("DEMO") == 10
        # Synthetic status is confined to this temporary-ledger mechanics test.
        status = MarketEvent(1, NOW-timedelta(milliseconds=500), "DEMO", SOURCE,
                             MarketEventType.RESUME, ResumePayload("synthetic test"))
        assert feed.process(token, status) == "RESUMED"
        assert feed.process(token, event) == "PROCESSED"
        assert host._quantity("DEMO") == 7
        assert feed.process(token, event) == "DUPLICATE_OR_OLDER_QUOTE"
        assert host._quantity("DEMO") == 7
    finally:
        feed.close()
        host.close()
