from datetime import UTC, date, datetime
from decimal import Decimal
import json
import httpx
import pytest

from app.trade_intelligence.knowledge.acquisition import (
    AcquisitionConfig, AlpacaHistoricalClient, PartitionManifest, detect_intraday_gaps,
    MalformedProviderResponse, normalize_bar, validate_source_bars,
)
from app.trade_intelligence.knowledge.models import HistoricalBar
from app.trade_intelligence.knowledge.candidate_days import attach_previous_closes, candidate_from_record, discover_candidate_days
from app.trade_intelligence.knowledge.orchestration import ResearchOrchestrator, RunPlan


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


def test_malformed_json_retries_then_succeeds_without_empty_conversion():
    attempts = 0
    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, content=b"<temporary gateway body>", request=request)
        return httpx.Response(200, json={"bars": []}, request=request)
    client = _client(handler, max_retries=1)
    try:
        assert client.fetch_minute_bars("ABC", datetime(2025, 1, 2, tzinfo=UTC), datetime(2025, 1, 3, tzinfo=UTC)) == ()
        assert client.request_counters["malformed_responses"] == 1
        assert client.request_counters["malformed_retries"] == 1
    finally: client.close()
    assert attempts == 2


def test_malformed_shape_retries_then_succeeds():
    attempts = 0
    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, json={"bars": {"ABC": "not-a-list"}}, request=request)
        return httpx.Response(200, json={"bars": []}, request=request)
    client = _client(handler, max_retries=1)
    try:
        assert client.fetch_minute_bars("ABC", datetime(2025, 1, 2, tzinfo=UTC), datetime(2025, 1, 3, tzinfo=UTC)) == ()
    finally: client.close()
    assert attempts == 2


def test_malformed_response_exhaustion_is_not_empty():
    def handler(request):
        return httpx.Response(200, content=b"not-json", request=request)
    client = _client(handler, max_retries=1)
    try:
        with pytest.raises(MalformedProviderResponse, match="MALFORMED_RESPONSE"):
            client.fetch_minute_bars("ABC", datetime(2025, 1, 2, tzinfo=UTC), datetime(2025, 1, 3, tzinfo=UTC))
        assert client.request_counters["malformed_responses"] == 2
    finally: client.close()


def test_malformed_second_page_does_not_commit_partial_download():
    attempts = 0
    def handler(request):
        nonlocal attempts
        attempts += 1
        if request.url.params.get("page_token") is None:
            return httpx.Response(200, json={"bars": [{"t": "2025-01-02T14:30:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 3}],
                                             "next_page_token": "second"}, request=request)
        return httpx.Response(200, content=b"bad-page", request=request)
    client = _client(handler, max_retries=1)
    try:
        with pytest.raises(MalformedProviderResponse):
            client.fetch_minute_bars("ABC", datetime(2025, 1, 2, tzinfo=UTC), datetime(2025, 1, 3, tzinfo=UTC))
    finally: client.close()
    assert attempts == 3


def test_one_exhausted_partition_isolated_and_next_partition_continues(tmp_path, monkeypatch):
    first = candidate_from_record({"symbol": "BAD", "trading_date": "2025-01-02", "reasons": ["DAILY_MOVE"],
                                   "open": "2", "high": "2", "low": "2", "close": "2", "volume": "0",
                                   "previous_close": None, "gap_percent": None, "change_percent": "0",
                                   "range_percent": "0", "dollar_volume": "0"})
    second = candidate_from_record({"symbol": "GOOD", "trading_date": "2025-01-02", "reasons": ["DAILY_MOVE"],
                                    "open": "1", "high": "2", "low": "1", "close": "2", "volume": "100",
                                    "previous_close": None, "gap_percent": None, "change_percent": "100",
                                    "range_percent": "100", "dollar_volume": "200"})
    plan = RunPlan("ALPACA", "IEX", date(2025, 1, 2), date(2025, 1, 2), 5000)
    orchestrator = ResearchOrchestrator(plan, root=tmp_path / "run", corpus_root=tmp_path / "corpus")
    monkeypatch.setattr(orchestrator, "prepare_candidate_plan", lambda symbols, allow_network: {
        "plan_id": "plan", "candidate_artifact_sha256": "hash", "candidates": (first, second)})

    class FakeClient:
        request_counters = {"requests": 0, "pages": 0, "429": 0, "5xx": 0, "retries": 0,
                            "malformed_responses": 0, "malformed_retries": 0}
        def close(self): pass

    monkeypatch.setattr("app.trade_intelligence.knowledge.orchestration.AlpacaHistoricalClient.from_environment",
                        classmethod(lambda cls, **kwargs: FakeClient()))
    calls = []
    def fake_download(client, config, symbol, trading_date, *, previous_close=None):
        calls.append(symbol)
        if symbol == "BAD":
            raise MalformedProviderResponse(path="/v2/stocks/BAD/bars")
        return PartitionManifest("ALPACA", "IEX", "SINGLE_EXCHANGE_FREE_RESEARCH", symbol,
                                 trading_date.isoformat(), "1Min", "start", "end", "now", 0,
                                 None, None, "hash", 1, "EMPTY_CONFIRMED")
    monkeypatch.setattr("app.trade_intelligence.knowledge.orchestration.download_partition", fake_download)
    result = orchestrator.run_alpaca(("BAD", "GOOD"), repository_commit="test")
    assert calls == ["BAD", "GOOD"]
    assert result["candidate_days"] == 2
    saved = [json.loads(line) for line in (tmp_path / "run" / "manifests" / "BAD_2025-01-02.json").read_text(encoding="utf-8").splitlines()]
    assert saved[0]["status"] == "FAILED_TRANSIENT_EXHAUSTED"
    assert json.loads((tmp_path / "run" / "run_manifest.json").read_text(encoding="utf-8").splitlines()[0])["final_status"] == "SUCCEEDED_WITH_PARTITION_FAILURES"


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


def test_daily_symbol_batches_are_stable_and_bounded():
    calls = []
    def handler(request):
        calls.append(tuple(request.url.params["symbols"].split(",")))
        return httpx.Response(200, json={"bars": []}, request=request)
    client = _client(handler, daily_symbol_batch_size=2)
    try:
        assert client.fetch_daily_bars_batched(("ZZZ", "AAA", "BBB", "CCC", "DDD"),
                                               datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 3, tzinfo=UTC)) == ()
    finally: client.close()
    assert calls == [("AAA", "BBB"), ("CCC", "DDD"), ("ZZZ",)]


def test_multi_symbol_daily_response_is_flattened_with_symbol_identity():
    def handler(request):
        return httpx.Response(200, json={"bars": {
            "AAA": [{"t": "2025-01-02T00:00:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 3}],
            "BBB": [{"t": "2025-01-02T00:00:00Z", "o": 3, "h": 4, "l": 3, "c": 4, "v": 5}],
        }}, request=request)
    client = _client(handler)
    try:
        rows = client.fetch_daily_bars(("BBB", "AAA"), datetime(2025, 1, 1, tzinfo=UTC),
                                       datetime(2025, 1, 3, tzinfo=UTC))
    finally:
        client.close()
    assert [(row["S"], row["c"]) for row in rows] == [("AAA", 2), ("BBB", 4)]


def test_previous_close_uses_latest_prior_observation_not_calendar_day():
    rows = [
        {"symbol": "ABC", "trading_date": "2025-01-03", "close": "10"},
        {"symbol": "ABC", "trading_date": "2025-01-06", "close": "12"},
        {"symbol": "ABC", "trading_date": "2025-01-07", "close": "13"},
    ]
    result = attach_previous_closes(rows, start_date=date(2025, 1, 6))
    assert result[0]["previous_close"] == "10"
    assert result[1]["previous_close"] == "12"
    assert discover_candidate_days([{"symbol": "ABC", "trading_date": "2025-01-06", "open": "13",
                                     "high": "14", "low": "12", "close": "13", "volume": "100",
                                     "previous_close": "10"}])[0].gap_percent == Decimal("30")
