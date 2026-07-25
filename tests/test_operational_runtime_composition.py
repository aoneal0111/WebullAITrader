from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.composition.operational_runtime import (
    OperationalRuntimeComposition,
    create_operational_runtime_composition,
)
from app.configuration.models import (
    OperationalConfiguration,
    TradingEnvironment,
)


def configuration(tmp_path) -> OperationalConfiguration:
    return OperationalConfiguration(
        environment=TradingEnvironment.SANDBOX,
        broker_provider="webull",
        account_id="acct",
        api_key="key",
        api_secret="secret",
        api_base_url="https://api.sandbox.webull.com",
        stream_url="wss://sandbox.example/ws",
        authorization_database_path=tmp_path / "auth.db",
        execution_database_path=tmp_path / "exec.db",
        market_event_database_path=tmp_path / "market.db",
        emergency_stop_database_path=tmp_path / "stop.db",
        log_level="INFO",
        health_port=8080,
        live_trading_enabled=False,
        max_order_notional=Decimal("10"),
        max_daily_notional=Decimal("50"),
        max_open_positions=1,
        max_open_orders=1,
        max_order_rate=5,
        max_quantity_per_symbol=Decimal("1"),
        allowed_symbols=("AAPL",),
        blocked_symbols=(),
        maximum_market_data_age_seconds=5,
        reconciliation_interval_seconds=30,
        maximum_reconciliation_age_seconds=60,
        maximum_unresolved_mutations=0,
    )


def test_operational_runtime_composition_wires_configured_dependencies(
    tmp_path,
):
    configured = configuration(tmp_path)
    clock = object()
    authorization_registry = object()
    execution_journal = object()
    market_store = object()
    emergency_stop = object()
    broker = object()
    captured = {}

    def authorization_registry_factory(path):
        captured["authorization_path"] = path
        return authorization_registry

    def execution_journal_factory(path):
        captured["execution_path"] = path
        return execution_journal

    def market_store_factory(path):
        captured["market_path"] = path
        return market_store

    def emergency_stop_factory(path, supplied_clock):
        captured["emergency_stop_path"] = path
        captured["clock"] = supplied_clock
        return emergency_stop

    def broker_factory(supplied_configuration):
        captured["broker_configuration"] = supplied_configuration
        return broker

    composition = create_operational_runtime_composition(
        configuration=configured,
        clock=lambda: clock,
        broker_factory=broker_factory,
        authorization_registry_factory=authorization_registry_factory,
        execution_journal_factory=execution_journal_factory,
        market_store_factory=market_store_factory,
        emergency_stop_factory=emergency_stop_factory,
    )

    assert composition == OperationalRuntimeComposition(
        configuration=configured,
        authorization_registry=authorization_registry,
        execution_journal=execution_journal,
        market_store=market_store,
        emergency_stop=emergency_stop,
        broker=broker,
    )
    assert captured["authorization_path"] == (
        configured.authorization_database_path
    )
    assert captured["execution_path"] == (
        configured.execution_database_path
    )
    assert captured["market_path"] == (
        configured.market_event_database_path
    )
    assert captured["emergency_stop_path"] == (
        configured.emergency_stop_database_path
    )
    assert captured["broker_configuration"] is configured
    assert captured["clock"]() is clock


def test_operational_runtime_composition_does_not_start_dependencies(
    tmp_path,
):
    configured = configuration(tmp_path)

    class InactiveDependency:
        def connect(self):
            raise AssertionError("composition must not connect dependencies")

        def reachable(self):
            raise AssertionError(
                "composition must not probe dependencies"
            )

        def close(self):
            raise AssertionError("composition must not close dependencies")

    dependency = InactiveDependency()

    composition = create_operational_runtime_composition(
        configuration=configured,
        clock=lambda: None,
        broker_factory=lambda value: dependency,
        authorization_registry_factory=lambda path: dependency,
        execution_journal_factory=lambda path: dependency,
        market_store_factory=lambda path: dependency,
        emergency_stop_factory=lambda path, clock: dependency,
    )

    assert composition.broker is dependency
    assert composition.authorization_registry is dependency
    assert composition.execution_journal is dependency
    assert composition.market_store is dependency
    assert composition.emergency_stop is dependency


@pytest.mark.parametrize(
    "argument_name",
    (
        "clock",
        "broker_factory",
        "authorization_registry_factory",
        "execution_journal_factory",
        "market_store_factory",
        "emergency_stop_factory",
    ),
)
def test_operational_runtime_composition_rejects_noncallable_factories(
    tmp_path,
    argument_name,
):
    arguments = {
        "configuration": configuration(tmp_path),
        "clock": lambda: None,
        "broker_factory": lambda value: object(),
        "authorization_registry_factory": lambda path: object(),
        "execution_journal_factory": lambda path: object(),
        "market_store_factory": lambda path: object(),
        "emergency_stop_factory": lambda path, clock: object(),
    }
    arguments[argument_name] = None

    with pytest.raises(
        TypeError,
        match=rf"{argument_name} must be callable",
    ):
        create_operational_runtime_composition(**arguments)


def test_operational_runtime_composition_requires_operational_configuration():
    with pytest.raises(
        TypeError,
        match="configuration must be OperationalConfiguration",
    ):
        create_operational_runtime_composition(
            configuration=SimpleNamespace(),
            clock=lambda: None,
            broker_factory=lambda value: object(),
        )

