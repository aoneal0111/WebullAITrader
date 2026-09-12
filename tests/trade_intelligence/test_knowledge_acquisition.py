from datetime import UTC, date, datetime
from decimal import Decimal
import httpx
import pytest

from app.trade_intelligence.knowledge.acquisition import (
    AcquisitionConfig, AlpacaHistoricalClient, detect_intraday_gaps,
    normalize_bar, validate_source_bars,
)
from app.trade_intelligence.knowledge.models import HistoricalBar


def _client(handler, **kwargs):
    return AlpacaHistoricalClient("key", "secret", config=AcquisitionConfig(requests_per_minute=100000, **kwargs),
                                 transport=httpx.MockTransport(handler), sleep=lambda _: None)


def test_alpaca_pagination_and_iex_provenance():
    calls = []
    def handler(request):
        calls.append(request.url.params.get("page_token"))
        payload = {"bars": [{"t": "2025-01-02T14:30:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 3}]}
        if len(calls) == 1: payload["next_page_token"] = "next"
        return httpx.Response(200, json=payload, request=request)
    client = _client(handler)
    try:
        rows = client.fetch_minute_bars("ABC", datetime(2025, 1, 2, tzinfo=UTC), datetime(2025, 1, 3, tzinfo=UTC))
    finally: client.close()
    assert len(rows) == 2 and calls == [None, "next"]
    bar = normalize_bar(rows[0], symbol="ABC", trading_date=date(2025, 1, 2))
    assert bar.provider == "ALPACA" and bar.feed == "IEX" and bar.session == "REGULAR"


def test_repeated_page_token_fails_closed():
    def handler(request):
        return httpx.Response(200, json={"bars": [], "next_page_token": "same"}, request=request)
    client = _client(handler, max_pages=3)
    try:
        with pytest.raises(RuntimeError, match="PAGINATION_LOOP"):
            client.fetch_minute_bars("ABC", datetime(2025, 1, 2, tzinfo=UTC), datetime(2025, 1, 3, tzinfo=UTC))
    finally: client.close()


def test_429_retries_without_becoming_empty():
    attempts = 0
    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1: return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"bars": []}, request=request)
    client = _client(handler, max_retries=1)
    try:
        assert client.fetch_minute_bars("ABC", datetime(2025, 1, 2, tzinfo=UTC), datetime(2025, 1, 3, tzinfo=UTC)) == ()
    finally: client.close()
    assert attempts == 2


def test_source_conflict_quarantines_and_identical_duplicates_collapse():
    base = dict(symbol="ABC", timestamp=datetime(2025, 1, 2, 14, 30, tzinfo=UTC), open=Decimal("1"),
                high=Decimal("2"), low=Decimal("1"), close=Decimal("2"), volume=Decimal("3"), session="REGULAR")
    one = HistoricalBar(**base); same = HistoricalBar(**base)
    assert len(validate_source_bars((one, same))) == 1
    conflict = HistoricalBar(**{**base, "close": Decimal("1.5")})
    with pytest.raises(ValueError, match="BAR_CONFLICT"): validate_source_bars((one, conflict))


def test_gap_detection_reports_without_fabricating_bars():
    common = dict(symbol="ABC", open=Decimal("1"), high=Decimal("2"), low=Decimal("1"), close=Decimal("2"), volume=Decimal("1"), session="REGULAR")
    rows = (HistoricalBar(timestamp=datetime(2025, 1, 2, 14, 30, tzinfo=UTC), **common),
            HistoricalBar(timestamp=datetime(2025, 1, 2, 14, 35, tzinfo=UTC), **common))
    assert detect_intraday_gaps(rows)[0]["missing_minutes"] == 4
