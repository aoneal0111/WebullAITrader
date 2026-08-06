from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import logging
from types import SimpleNamespace

import pytest

from app.configuration import MarketDataConfiguration, TradingEnvironment
from app.webull.logging import StructuredLogger, sanitized_sdk_event
from app.webull.market_data_probe import MarketDataCapabilityProbe, ProbeState
from app.webull.market_data_session import (
    MarketDataSession,
    current_market_data_session,
    next_premarket_start,
    requires_overnight_entitlement,
)
from app.webull.sdk_market_data import (
    LazyOfficialDataClient,
    configure_official_sdk_logging,
)
from app.webull.sdk_streaming_adapter import WebullMarketSubscription


OBSERVED = datetime(2026, 8, 6, 2, 5, 25, tzinfo=UTC)


class _Response:
    status_code = 200

    def json(self):
        return {"data": [{"symbol": "AAPL"}]}


class _RestClient:
    def __init__(self):
        self.market_data = SimpleNamespace(
            get_history_bar=lambda *args, **kwargs: _Response(),
            get_quotes=lambda *args, **kwargs: _Response(),
            get_snapshot=lambda *args, **kwargs: _Response(),
        )
        self.instrument = SimpleNamespace(
            get_instrument=lambda *args, **kwargs: _Response()
        )


class _ProductDeniedStream:
    heartbeat_ok = True
    reconnect_ready = True
    subscription_acknowledged = True

    def __init__(self):
        self.subscriptions = []

    def connect(self):
        return None

    def subscribe(self, symbols):
        self.subscriptions.append(tuple(symbols))
        raise PermissionError(
            "HTTP 403 MARKET_DATA_NOT_SUBSCRIBED: "
            "subscribe to US_STOCK OVERNIGHT token=never-log"
        )


def _configuration() -> MarketDataConfiguration:
    return MarketDataConfiguration(
        TradingEnvironment.LIVE,
        "key",
        "secret",
        "https://data.example",
        "wss://stream.example/mqtt",
    )


def test_observed_timestamp_is_new_york_overnight_and_requests_entitlement():
    eastern = OBSERVED.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))

    assert eastern.isoformat() == "2026-08-05T22:05:25-04:00"
    assert current_market_data_session(lambda: OBSERVED) is MarketDataSession.OVERNIGHT
    assert requires_overnight_entitlement(lambda: OBSERVED) is True
    assert next_premarket_start(lambda: OBSERVED).isoformat() == "2026-08-06T04:00:00-04:00"
    arguments = WebullMarketSubscription(
        "US_STOCK", ("QUOTE",), clock=lambda: OBSERVED
    ).sdk_arguments(("AAPL",))
    assert arguments["overnight_required"] is True


@pytest.mark.parametrize(
    ("instant", "session"),
    (
        (datetime(2026, 8, 6, 8, tzinfo=UTC), MarketDataSession.PREMARKET),
        (datetime(2026, 8, 6, 15, tzinfo=UTC), MarketDataSession.REGULAR),
        (datetime(2026, 8, 6, 21, tzinfo=UTC), MarketDataSession.AFTER_HOURS),
        (datetime(2026, 8, 8, 16, tzinfo=UTC), MarketDataSession.CLOSED),
        (datetime(2026, 9, 7, 6, tzinfo=UTC), MarketDataSession.CLOSED),
    ),
)
def test_non_overnight_sessions_never_request_overnight_access(instant, session):
    assert current_market_data_session(lambda: instant) is session
    assert requires_overnight_entitlement(lambda: instant) is False


def test_product_entitlement_denial_short_circuits_and_is_session_cached():
    current = [OBSERVED]
    stream = _ProductDeniedStream()
    probe = MarketDataCapabilityProbe(
        _configuration(),
        LazyOfficialDataClient(_RestClient),
        stream,
        clock=lambda: current[0],
    )

    first = probe.run()
    repeated = probe.run()

    assert first.reason == "OVERNIGHT_ENTITLEMENT_REQUIRED"
    assert first.entitlement_code == "OVERNIGHT_ENTITLEMENT_REQUIRED"
    assert first.entitlement.state is ProbeState.NOT_ENTITLED
    assert first.avoided_subscription_requests == 4
    assert len(stream.subscriptions) == 1
    assert repeated.cached is True
    assert len(stream.subscriptions) == 1
    assert "never-log" not in first.subscription.detail

    current[0] = datetime(2026, 8, 6, 8, tzinfo=UTC)
    transitioned = probe.run()
    assert transitioned.cached is False
    assert len(stream.subscriptions) == 2


def test_explicit_configuration_change_releases_same_session_probe_gate():
    stream = _ProductDeniedStream()
    probe = MarketDataCapabilityProbe(
        _configuration(), LazyOfficialDataClient(_RestClient), stream,
        clock=lambda: OBSERVED,
    )
    probe.run()
    probe.configuration_changed()
    probe.run()
    assert len(stream.subscriptions) == 2


def test_sdk_diagnostic_is_allow_listed_and_preserves_request_id():
    event = sanitized_sdk_event(
        status="failed",
        http_status=403,
        error_code="MARKET_DATA_NOT_SUBSCRIBED",
        endpoint_path="/market-data/subscriptions",
        capability="overnight_subscription",
        environment="PRODUCTION",
        request_id="request-visible-123",
    )

    assert event["request_id"] == "request-visible-123"
    assert set(event) == {
        "operation", "status", "http_status", "error_code",
        "endpoint_path", "capability", "environment", "request_id",
    }


def test_structured_logging_redacts_required_sdk_secrets_and_keeps_request_id():
    records = []
    sink = SimpleNamespace(emit=records.append)
    StructuredLogger(sink).log(
        "subscription", "failed", request_id="request-visible-123",
        x_app_key="key-secret", x_access_token="token-secret",
        x_signature="signature-secret", x_signature_nonce="nonce-secret",
        authorization="Bearer auth-secret", app_secret="app-secret",
        api_secret="api-secret", token="token-secret", cookie="cookie-secret",
    )
    rendered = repr(records)
    assert "request-visible-123" in rendered
    for secret in ("key-secret", "token-secret", "signature-secret", "nonce-secret", "auth-secret", "app-secret", "api-secret", "cookie-secret"):
        assert secret not in rendered


def test_concurrent_sdk_logger_initialization_has_one_non_rotating_handler():
    with ThreadPoolExecutor(max_workers=8) as pool:
        loggers = tuple(pool.map(lambda _: configure_official_sdk_logging(), range(64)))

    logger = logging.getLogger("webull.core")
    assert all(item is logger for item in loggers)
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.NullHandler)
    assert not any(hasattr(handler, "doRollover") for handler in logger.handlers)
