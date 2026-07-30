from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from app.broker_plugins import (
    BrokerPluginRegistry,
    BrokerRuntime,
)
from app.broker_plugins.webull import (
    WEBULL_CAPABILITIES,
    WebullBrokerPlugin,
    create_webull_runtime,
)
from app.broker_protocol.models import (
    BrokerAccount,
    BrokerCash,
)


@dataclass(frozen=True, slots=True)
class FakeConfiguration:
    account_id: str = "test-account"


class FakeBroker:
    def __init__(self) -> None:
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def submit_order(self, order):
        return order

    def cancel_order(self, client_order_id: str):
        return client_order_id

    def replace_order(self, client_order_id: str, order):
        return client_order_id, order

    def get_positions(self) -> tuple:
        return ()

    def get_orders(self) -> tuple:
        return ()

    def get_cash(self) -> BrokerCash:
        return BrokerCash(
            settled_cash=Decimal("1000"),
            unsettled_cash=None,
            currency="USD",
        )

    def get_account(self) -> BrokerAccount:
        return BrokerAccount(
            account_id_redacted="****1234",
            account_type="PAPER",
            status="ACTIVE",
        )

    def get_fills(self) -> tuple:
        return ()


def test_webull_capabilities_include_existing_streaming_stack() -> None:
    assert WEBULL_CAPABILITIES.provider == "webull"
    assert WEBULL_CAPABILITIES.supports_execution is True
    assert WEBULL_CAPABILITIES.supports_account_data is True
    assert WEBULL_CAPABILITIES.supports_market_data is True
    assert WEBULL_CAPABILITIES.supports_streaming is True
    assert WEBULL_CAPABILITIES.supports_scanner is False


def test_webull_plugin_exposes_expected_metadata() -> None:
    plugin = WebullBrokerPlugin(
        broker_factory=lambda configuration: FakeBroker()
    )

    assert plugin.provider == "webull"
    assert plugin.capabilities is WEBULL_CAPABILITIES


def test_webull_plugin_creates_execution_runtime() -> None:
    configuration = FakeConfiguration()
    broker = FakeBroker()
    observed_configurations: list[object] = []

    def broker_factory(value: object) -> FakeBroker:
        observed_configurations.append(value)
        return broker

    plugin = WebullBrokerPlugin(
        broker_factory=broker_factory
    )

    runtime = plugin.create_runtime(configuration)

    assert isinstance(runtime, BrokerRuntime)
    assert runtime.provider == "webull"
    assert runtime.capabilities is WEBULL_CAPABILITIES
    assert runtime.execution is broker
    assert runtime.market_data is None
    assert runtime.scanner is None
    assert observed_configurations == [configuration]


def test_webull_plugin_composes_injected_market_data_transport() -> None:
    configuration = FakeConfiguration()
    broker = FakeBroker()
    market_data = object()

    plugin = WebullBrokerPlugin(
        broker_factory=lambda value: broker,
        market_data_factory=lambda value: market_data,
    )

    runtime = plugin.create_runtime(configuration)

    assert runtime.execution is broker
    assert runtime.market_data is market_data


def test_webull_plugin_can_be_registered_and_resolved() -> None:
    broker = FakeBroker()
    plugin = WebullBrokerPlugin(
        broker_factory=lambda configuration: broker
    )
    registry = BrokerPluginRegistry()

    registry.register(plugin)
    runtime = registry.create_runtime(
        "WEBULL",
        FakeConfiguration(),
    )

    assert registry.providers() == ("webull",)
    assert runtime.execution is broker


def test_create_webull_runtime_rejects_non_callable_factory() -> None:
    with pytest.raises(
        ValueError,
        match="broker_factory must be callable",
    ):
        create_webull_runtime(
            FakeConfiguration(),
            broker_factory=None,  # type: ignore[arg-type]
        )


def test_create_webull_runtime_rejects_empty_factory_result() -> None:
    with pytest.raises(
        ValueError,
        match="returned no broker",
    ):
        create_webull_runtime(
            FakeConfiguration(),
            broker_factory=lambda configuration: None,
        )
