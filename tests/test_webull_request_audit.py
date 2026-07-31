from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.configuration import (
    MarketDataConfiguration,
    TradingConfiguration,
    TradingEnvironment,
)
from app.webull.http_client import WebullHttpClient
from app.webull.request_audit import (
    AuditedMarketDataClient,
    RequestIdentity,
    RequestIsolationError,
    RequestIsolationGuard,
    RequestService,
)


def configurations(*, same_identity=False, same_environment=False):
    trading = TradingConfiguration(
        TradingEnvironment.TEST,
        "paper-account",
        "trade-key",
        "trade-secret",
        "https://api.sandbox.webull.com",
        "wss://data-api.sandbox.webull.com/mqtt",
    )
    market = MarketDataConfiguration(
        TradingEnvironment.TEST if same_environment else TradingEnvironment.PRODUCTION,
        "trade-key" if same_identity else "data-key",
        "trade-secret" if same_identity else "data-secret",
        "https://api.webull.com",
        "wss://data-api.webull.com/mqtt",
    )
    return trading, market


def test_cross_environment_services_require_distinct_fingerprints():
    trading, market = configurations(same_identity=True)
    with pytest.raises(RequestIsolationError, match="distinct identities"):
        RequestIsolationGuard(trading, market)


def test_legacy_same_environment_identity_remains_compatible():
    trading, market = configurations(same_identity=True, same_environment=True)
    guard = RequestIsolationGuard(trading, market)
    assert guard.identity(RequestService.TRADING).fingerprint == (
        guard.identity(RequestService.MARKET_DATA).fingerprint
    )


def test_market_data_calls_record_only_market_data_identity(caplog):
    trading, market = configurations()
    guard = RequestIsolationGuard(trading, market)
    calls = []
    raw = SimpleNamespace(
        market_data=SimpleNamespace(
            get_quotes=lambda symbol, category: calls.append((symbol, category)) or []
        )
    )
    client = AuditedMarketDataClient(raw, guard, market)

    with caplog.at_level("INFO", logger="atlas.webull.request_audit"):
        assert client.market_data.get_quotes("AAPL", "US_STOCK") == []

    records = guard.records
    assert [record.capability_result for record in records] == [
        "REQUESTED", "SUCCEEDED"
    ]
    assert all(record.service == "MARKET_DATA" for record in records)
    assert all(record.environment == "PRODUCTION" for record in records)
    assert all(record.fingerprint.startswith("fp_") for record in records)
    output = caplog.text
    assert "data-key" not in output
    assert "data-secret" not in output


def test_trading_calls_record_only_trading_identity():
    trading, market = configurations()
    guard = RequestIsolationGuard(trading, market)
    trade_client = SimpleNamespace(
        account_v2=SimpleNamespace(get_account_list=lambda: []),
        order_v3=SimpleNamespace(),
    )
    limiter = SimpleNamespace(acquire=lambda: None)
    logger = SimpleNamespace(log=lambda *args, **kwargs: None)
    client = WebullHttpClient(
        trade_client,
        limiter,
        logger,
        request_guard=guard,
        request_identity=guard.identity(RequestService.TRADING),
        endpoint=trading.api_base_url,
    )

    assert client.get("/openapi/account/list") == []
    assert all(record.service == "TRADING" for record in guard.records)
    assert all(record.environment == "TEST" for record in guard.records)
    assert all("sandbox.webull.com" in record.endpoint for record in guard.records)


def test_forged_service_identity_aborts_before_request_dispatch():
    trading, market = configurations()
    guard = RequestIsolationGuard(trading, market)
    expected = guard.identity(RequestService.TRADING)
    forged = RequestIdentity(
        RequestService.MARKET_DATA,
        expected.environment,
        expected.fingerprint,
    )
    calls = []
    client = WebullHttpClient(
        SimpleNamespace(
            account_v2=SimpleNamespace(
                get_account_list=lambda: calls.append("called") or []
            ),
            order_v3=SimpleNamespace(),
        ),
        SimpleNamespace(acquire=lambda: None),
        SimpleNamespace(log=lambda *args, **kwargs: None),
        request_guard=guard,
        request_identity=forged,
        endpoint=trading.api_base_url,
    )

    with pytest.raises(RequestIsolationError):
        client.get("/openapi/account/list")
    assert calls == []


def test_production_signal_can_only_submit_sandbox_paper_orders():
    trading, market = configurations()
    guard = RequestIsolationGuard(trading, market)
    market_calls = []
    market_client = AuditedMarketDataClient(
        SimpleNamespace(
            market_data=SimpleNamespace(
                get_quotes=lambda symbol, category: (
                    market_calls.append((symbol, category))
                    or [{"symbol": symbol, "price": "200.00"}]
                )
            )
        ),
        guard,
        market,
    )
    submitted = []
    trading_client = WebullHttpClient(
        SimpleNamespace(
            account_v2=SimpleNamespace(),
            order_v3=SimpleNamespace(
                place_order=lambda account_id, orders, **kwargs: (
                    submitted.append((account_id, orders[0]["side"]))
                    or {"client_order_id": f"paper-{len(submitted)}"}
                )
            ),
        ),
        SimpleNamespace(acquire=lambda: None),
        SimpleNamespace(log=lambda *args, **kwargs: None),
        request_guard=guard,
        request_identity=guard.identity(RequestService.TRADING),
        endpoint=trading.api_base_url,
    )

    production_quote = market_client.market_data.get_quotes(
        "NVDA", "US_STOCK"
    )
    assert production_quote[0]["price"] == "200.00"
    for side in ("BUY", "SELL"):
        trading_client.post(
            "/openapi/trade/order/place",
            payload={
                "account_id": trading.account_id,
                "new_orders": [{"symbol": "NVDA", "side": side}],
            },
        )

    assert market_calls == [("NVDA", "US_STOCK")]
    assert submitted == [
        ("paper-account", "BUY"),
        ("paper-account", "SELL"),
    ]
    records = guard.records
    assert any(
        record.service == "MARKET_DATA"
        and record.environment == "PRODUCTION"
        and record.endpoint.startswith("https://api.webull.com/")
        for record in records
    )
    order_records = [
        record
        for record in records
        if "/openapi/trade/order/place" in record.endpoint
    ]
    assert order_records
    assert all(record.service == "TRADING" for record in order_records)
    assert all(record.environment == "TEST" for record in order_records)
    assert all("api.sandbox.webull.com" in record.endpoint for record in order_records)
    assert not any(
        record.service == "TRADING"
        and record.endpoint.startswith("https://api.webull.com/")
        for record in records
    )
