from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app.market_data.models import MarketEvent, MarketEventType, ResumePayload, TradingHaltPayload
from app.paper_trading.protection_feed import PaperProtectionFeed
from app.paper_trading.protection_host import PaperProtectionHost
from app.paper_trading.protection_host_check import T0, seed, quote


@pytest.fixture
def setup(tmp_path):
    path = tmp_path / "paper-execution.sqlite3"
    seed(path)
    wall, mono = [T0 + timedelta(seconds=2)], [100.0]
    host = PaperProtectionHost(path, environment="PAPER", clock=lambda: wall[0])
    feed = PaperProtectionFeed(host, source="synthetic-protection-check",
                               silence_seconds=5, clock=lambda: mono[0])
    def event(kind=MarketEventType.QUOTE, **kwargs):
        wall[0] += timedelta(milliseconds=100)
        mono[0] += .1
        if kind is MarketEventType.QUOTE:
            return quote(wall[0], **kwargs)
        payload = ResumePayload("synthetic") if kind is MarketEventType.RESUME else TradingHaltPayload("synthetic")
        return MarketEvent(2, wall[0], "DEMO", "synthetic-protection-check", kind, payload)
    yield host, feed, wall, mono, event
    host.close()


def test_connect_never_implies_tradability_and_new_resume_allows_exit(setup):
    host, feed, _, _, event = setup
    token = feed.connect()
    assert feed.process(token, event()) == "HALTED"
    assert host._quantity("DEMO") == 10
    assert feed.process(token, event(MarketEventType.RESUME)) == "RESUMED"
    assert feed.process(token, event()) == "PROCESSED"
    assert host._quantity("DEMO") == 0


def test_disconnect_preserves_stop_and_requires_newer_status_after_reconnect(setup):
    host, feed, _, _, event = setup
    old = feed.connect()
    prior_status = event(MarketEventType.RESUME)
    assert feed.process(old, prior_status) == "RESUMED"
    orders = [(o.order_id, o.remaining_quantity) for o in host._commands.order_book.open_orders()]
    assert feed.disconnect(old) == "DISCONNECTED"
    token = feed.connect()
    assert feed.process(old, event()) == "OLD_CONNECTION"
    assert feed.disconnect(old) == "OLD_CONNECTION"
    assert feed.process(token, prior_status) == "DUPLICATE_OR_OLDER_STATUS"
    assert feed.process(token, event()) == "HALTED"
    assert orders == [(o.order_id, o.remaining_quantity) for o in host._commands.order_book.open_orders()]
    assert feed.process(token, event(MarketEventType.RESUME)) == "RESUMED"
    assert feed.process(token, event()) == "PROCESSED"


def test_silence_deadline_revokes_generation_before_a_fresh_quote(setup):
    host, feed, wall, mono, event = setup
    token = feed.connect()
    feed.process(token, event(MarketEventType.RESUME))
    wall[0] += timedelta(seconds=5)
    mono[0] += 5
    assert feed.process(token, quote(wall[0])) == "FEED_SILENT"
    assert host.snapshot()["matching_paused_symbols"] == 1
    assert host._quantity("DEMO") == 10
    token = feed.connect()
    assert feed.process(token, event()) == "HALTED"


def test_watchdog_pauses_without_incoming_events(setup):
    host, feed, wall, mono, event = setup
    token = feed.connect()
    feed.process(token, event(MarketEventType.RESUME))
    wall[0] += timedelta(seconds=5)
    mono[0] += 5
    assert feed.check_health() == "FEED_SILENT"
    assert feed.snapshot()["connected"] is False
    assert host.snapshot()["matching_paused_symbols"] == 1


@pytest.mark.parametrize("bad", ["source", "stale", "invalid"])
def test_bad_current_generation_event_revokes_status_without_fill(setup, bad):
    host, feed, wall, _, event = setup
    token = feed.connect()
    feed.process(token, event(MarketEventType.RESUME))
    q = event()
    if bad == "source":
        assert feed.process(token, replace(q, source="other")) == "SOURCE_MISMATCH"
    elif bad == "stale":
        assert feed.process(token, replace(q, timestamp=wall[0]-timedelta(seconds=6))) == "STALE_OR_FUTURE_EVENT"
    else:
        with pytest.raises(ValueError):
            feed.process(token, replace(q, payload=replace(q.payload, bid_size=Decimal(-1))))
    assert not feed.snapshot()["connected"]
    assert host.snapshot()["matching_paused_symbols"] == 1
    assert host._quantity("DEMO") == 10


def test_duplicate_quotes_do_not_renew_silence_deadline(setup):
    host, feed, wall, mono, event = setup
    token = feed.connect()
    feed.process(token, event(MarketEventType.RESUME))
    q = event(size="4")
    assert feed.process(token, q) == "PROCESSED"
    for _ in range(4):
        mono[0] += 1
        wall[0] += timedelta(seconds=1)
        assert feed.process(token, q) == "DUPLICATE_OR_OLDER_QUOTE"
    mono[0] += 1
    wall[0] += timedelta(seconds=1)
    assert feed.check_health() == "FEED_SILENT"
    assert host._quantity("DEMO") == 6


def test_halt_requires_fresh_resume_even_on_same_connection(setup):
    host, feed, _, _, event = setup
    token = feed.connect()
    feed.process(token, event(MarketEventType.RESUME))
    assert feed.process(token, event(MarketEventType.TRADING_HALT)) == "HALTED"
    assert feed.process(token, event()) == "HALTED"
    assert host._quantity("DEMO") == 10
    feed.process(token, event(MarketEventType.RESUME))
    assert feed.process(token, event()) == "PROCESSED"


def test_monotonic_clock_fault_revokes_connection(setup):
    host, feed, _, mono, _ = setup
    feed.connect()
    mono[0] -= 1
    with pytest.raises(ValueError, match="backwards"):
        feed.check_health()
    assert host.snapshot()["matching_paused_symbols"] == 1


@pytest.mark.parametrize("limit", [0, -1, 6, True, float("inf"), float("nan")])
def test_invalid_timeout_rejected(setup, limit):
    host, _, _, _, _ = setup
    with pytest.raises(ValueError):
        PaperProtectionFeed(host, source="test", silence_seconds=limit)
