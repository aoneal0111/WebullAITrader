from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from webull.core.exception.exceptions import ClientException, ServerException
from webull.core.http.initializer.client_initializer import ClientInitializer

from app.webull.errors import (
    AuthenticationError,
    BrokerRejectionError,
    NetworkError,
    ValidationError,
)
from app.webull.http_client import (
    WebullHttpClient,
    create_official_trade_client,
)


class Limiter:
    def __init__(self) -> None:
        self.acquisitions = 0

    def acquire(self) -> None:
        self.acquisitions += 1


class Logger:
    def __init__(self) -> None:
        self.records = []

    def log(self, *args, **kwargs) -> None:
        self.records.append((args, kwargs))


class AccountApi:
    def __init__(self) -> None:
        self.calls = []

    def get_account_list(self):
        self.calls.append(("list",))
        return [{"account_id": "account-1"}]

    def get_account_position(self, account_id):
        self.calls.append(("positions", account_id))
        return [{"symbol": "AAPL"}]

    def get_account_balance(self, account_id):
        self.calls.append(("balance", account_id))
        return {"settled_cash": "100"}


class OrderApi:
    def __init__(self) -> None:
        self.calls = []

    def get_order_open(self, account_id, **kwargs):
        self.calls.append(("open", account_id, kwargs))
        return []

    def get_order_history(self, account_id, **kwargs):
        self.calls.append(("history", account_id, kwargs))
        return []

    def place_order(self, account_id, orders, **kwargs):
        self.calls.append(("place", account_id, orders, kwargs))
        return {"order_id": "placed"}

    def cancel_order(self, account_id, client_order_id):
        self.calls.append(("cancel", account_id, client_order_id))
        return {"order_id": "cancelled"}

    def replace_order(self, account_id, orders, **kwargs):
        self.calls.append(("replace", account_id, orders, kwargs))
        return {"order_id": "replaced"}


def client():
    trade = SimpleNamespace(account_v2=AccountApi(), order_v3=OrderApi())
    limiter = Limiter()
    return WebullHttpClient(trade, limiter, Logger()), trade, limiter


def test_account_positions_balances_and_orders_use_official_sdk_facades():
    sdk, trade, limiter = client()

    assert sdk.get("/openapi/account/list")[0]["account_id"] == "account-1"
    assert sdk.get(
        "/openapi/assets/positions",
        query={"account_id": "account-1"},
    )[0]["symbol"] == "AAPL"
    assert sdk.get(
        "/openapi/assets/balance",
        query={"account_id": "account-1"},
    )["settled_cash"] == "100"
    assert sdk.get(
        "/openapi/trade/order/open",
        query={"account_id": "account-1", "page_size": "100"},
    ) == []
    assert sdk.get(
        "/openapi/trade/order/history",
        query={"account_id": "account-1"},
    ) == []

    assert trade.account_v2.calls == [
        ("list",),
        ("positions", "account-1"),
        ("balance", "account-1"),
    ]
    assert [call[0] for call in trade.order_v3.calls] == ["open", "history"]
    assert limiter.acquisitions == 5


def test_order_mutations_use_official_sdk_facade():
    sdk, trade, _ = client()
    order = {"client_order_id": "client-1"}

    sdk.post(
        "/openapi/trade/order/place",
        payload={"account_id": "account-1", "new_orders": [order]},
    )
    sdk.post(
        "/openapi/trade/order/cancel",
        payload={
            "account_id": "account-1",
            "client_order_id": "client-1",
        },
    )
    sdk.post(
        "/openapi/trade/order/replace",
        payload={"account_id": "account-1", "modify_orders": [order]},
    )

    assert [call[0] for call in trade.order_v3.calls] == [
        "place",
        "cancel",
        "replace",
    ]


def test_official_client_factory_configures_sdk_and_runs_initializer(
    monkeypatch,
):
    observed = {}

    def initialize(api_client):
        observed["client"] = api_client
        api_client.set_token("sdk-initialized-token")

    monkeypatch.setattr(
        ClientInitializer,
        "initializer",
        staticmethod(initialize),
    )

    result = create_official_trade_client(
        app_key="app-key",
        app_secret="app-secret",
        endpoint="https://api.sandbox.webull.com",
        timeout_seconds=Decimal("10"),
    )

    assert result.account_v2 is not None
    assert result.order_v3 is not None
    assert observed["client"].get_app_key() == "app-key"
    assert observed["client"].get_app_secret() == "app-secret"
    assert observed["client"].get_token() == "sdk-initialized-token"
    assert observed["client"]._resolve_endpoint(object()) == (
        "api.sandbox.webull.com"
    )


@pytest.mark.parametrize(
    ("exception", "expected"),
    (
        (ServerException("denied", http_status=401), AuthenticationError),
        (ServerException("bad", http_status=400), BrokerRejectionError),
        (ServerException("down", http_status=503), NetworkError),
        (ClientException("ERROR_CREATE_TOKEN"), AuthenticationError),
    ),
)
def test_sdk_errors_are_mapped_to_existing_transport_errors(
    exception,
    expected,
):
    sdk, trade, _ = client()

    def fail():
        raise exception

    trade.account_v2.get_account_list = fail
    with pytest.raises(expected):
        sdk.get("/openapi/account/list")


def test_unsupported_paths_and_missing_account_ids_fail_closed():
    sdk, _, _ = client()
    with pytest.raises(ValidationError):
        sdk.get("/not-an-sdk-operation")
    with pytest.raises(ValidationError):
        sdk.get("/openapi/assets/positions")
