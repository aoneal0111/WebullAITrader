from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.broker_plugins import create_broker_runtime
from app.broker_plugins.exceptions import UnknownBrokerProviderError
from app.broker_plugins.models import BrokerRuntime


@dataclass(frozen=True, slots=True)
class FakeConfiguration:
    account_id: str = "test"


class FakeBroker:
    def connect(self):
        pass

    def disconnect(self):
        pass

    def submit_order(self, order):
        return order

    def cancel_order(self, client_order_id):
        return client_order_id

    def replace_order(self, client_order_id, order):
        return order

    def get_positions(self):
        return ()

    def get_orders(self):
        return ()

    def get_cash(self):
        return None

    def get_account(self):
        return None

    def get_fills(self):
        return ()


def test_create_broker_runtime_returns_runtime() -> None:
    broker = FakeBroker()

    runtime = create_broker_runtime(
        provider="webull",
        configuration=FakeConfiguration(),
        webull_broker_factory=lambda configuration: broker,
    )

    assert isinstance(runtime, BrokerRuntime)
    assert runtime.provider == "webull"
    assert runtime.execution is broker


def test_provider_lookup_is_case_insensitive() -> None:
    runtime = create_broker_runtime(
        provider="WEBULL",
        configuration=FakeConfiguration(),
        webull_broker_factory=lambda configuration: FakeBroker(),
    )

    assert runtime.provider == "webull"


def test_unknown_provider_raises() -> None:
    with pytest.raises(UnknownBrokerProviderError):
        create_broker_runtime(
            provider="alpaca",
            configuration=FakeConfiguration(),
            webull_broker_factory=lambda configuration: FakeBroker(),
        )
