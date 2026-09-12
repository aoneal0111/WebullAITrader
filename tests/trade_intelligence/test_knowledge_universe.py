import httpx

from app.trade_intelligence.knowledge.universe import AlpacaAssetMasterClient, normalize_assets, universe_report


def transport(request: httpx.Request) -> httpx.Response:
    assert request.method == "GET"
    assert request.url.host == "paper-api.alpaca.markets"
    assert request.url.path == "/v2/assets"
    assert request.url.params["asset_class"] == "us_equity"
    status = request.url.params["status"]
    rows = [{"id": "1", "symbol": "AAA", "status": status, "class": "us_equity", "exchange": "NASDAQ", "tradable": True},
            {"id": "2", "symbol": "OTC1", "status": status, "class": "us_equity", "exchange": "OTC"},
            {"id": "3", "symbol": "SPY", "status": status, "class": "us_equity", "exchange": "ARCA", "attributes": ["ETF"]}]
    return httpx.Response(200, json=rows)


def test_asset_master_is_get_assets_only_and_normalized(tmp_path):
    client = AlpacaAssetMasterClient("key", "secret", transport=httpx.MockTransport(transport), snapshot_root=tmp_path)
    try:
        snapshot = client.snapshot()
    finally:
        client.close()
    assert client.request_count == 2
    assert snapshot["provenance"] == "ALPACA_ASSET_MASTER_CURRENT_SNAPSHOT"
    assert snapshot["limitation"] == "NOT_POINT_IN_TIME_UNIVERSE"
    assert len(snapshot["raw_assets"]) == 6
    assert {item["symbol"] for item in snapshot["assets"] if item["included"]} == {"AAA"}
    assert universe_report(snapshot)["otc_policy"] == "EXCLUDED"
    assert list(tmp_path.rglob("universe.json"))


def test_snapshot_cache_reuses_without_network(tmp_path):
    first = AlpacaAssetMasterClient("key", "secret", transport=httpx.MockTransport(transport), snapshot_root=tmp_path)
    first.snapshot(); first.close()
    def fail(request):
        raise AssertionError("cache unexpectedly contacted network")
    second = AlpacaAssetMasterClient("key", "secret", transport=httpx.MockTransport(fail), snapshot_root=tmp_path)
    try:
        assert second.symbols() == ("AAA",)
        assert second.request_count == 0
    finally:
        second.close()


def test_duplicate_and_invalid_symbols_are_excluded():
    included, excluded = normalize_assets([
        {"symbol": "AAA", "class": "us_equity", "exchange": "NASDAQ"},
        {"symbol": "aaa", "class": "us_equity", "exchange": "NASDAQ"},
        {"symbol": "OTC", "class": "us_equity", "exchange": "OTC"},
        {"symbol": "TEST", "class": "us_equity", "exchange": "NYSE"},
    ])
    assert [item["symbol"] for item in included if item["included"]] == ["AAA"]
    assert excluded["DUPLICATE_OR_INVALID_SYMBOL"] == 1
    assert excluded["OTC_OR_UNSUPPORTED_EXCHANGE"] == 1
    assert excluded["INVALID_OR_TEST_SYMBOL"] == 1
