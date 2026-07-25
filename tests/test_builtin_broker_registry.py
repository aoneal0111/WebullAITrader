from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.broker_plugins import (
    BrokerPluginRegistry,
    create_builtin_broker_registry,
)
from app.broker_plugins.webull import WEBULL_CAPABILITIES


@dataclass(frozen=True, slots=True)
class FakeConfiguration:
    account_id: str = "test-account"


class FakeBroker:
    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

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

    def get_cash(self):
        return None

    def get_account(self):
        return None

    def get_fills(self) -> tuple:
        return ()


def test_builtin_registry_registers_webull() -> None:
    registry = create_builtin_broker_registry(
        webull_broker_factory=lambda configuration: FakeBroker(),
    )

    assert isinstance(registry, BrokerPluginRegistry)
    assert registry.providers() == ("webull",)
    assert registry.capabilities("webull") is WEBULL_CAPABILITIES


def test_builtin_registry_creates_webull_runtime() -> None:
    configuration = FakeConfiguration()
    broker = FakeBroker()
    observed_configurations: list[object] = []

    def broker_factory(value: object) -> FakeBroker:
        observed_configurations.append(value)
        return broker

    registry = create_builtin_broker_registry(
        webull_broker_factory=broker_factory,
    )

    runtime = registry.create_runtime(
        "WEBULL",
        configuration,
    )

    assert runtime.provider == "webull"
    assert runtime.execution is broker
    assert runtime.capabilities is WEBULL_CAPABILITIES
    assert observed_configurations == [configuration]


def test_builtin_registry_returns_new_registry_each_time() -> None:
    factory = lambda configuration: FakeBroker()

    first = create_builtin_broker_registry(
        webull_broker_factory=factory,
    )
    second = create_builtin_broker_registry(
        webull_broker_factory=factory,
    )

    assert first is not second
    assert first.providers() == second.providers() == ("webull",)


def test_builtin_registry_rejects_non_callable_factory() -> None:
    with pytest.raises(
        ValueError,
        match="webull_broker_factory must be callable",
    ):
        create_builtin_broker_registry(
            webull_broker_factory=None,  # type: ignore[arg-type]
        )
