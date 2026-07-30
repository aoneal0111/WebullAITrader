from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.broker_protocol.models import BrokerAccount, BrokerCash
from app.configuration.models import OperationalConfiguration, TradingEnvironment
from app import operational_main
from app.composition.operational_runtime import (
    OperationalRuntimeComposition,
)


class FakeClosable:
    def __init__(self, pending=()):
        self.pending = pending
        self.authorizations = ()
        self.closed = False

    def reachable(self):
        return True

    def close(self):
        self.closed = True


class FakeStop(FakeClosable):
    def __init__(self, enabled=True):
        super().__init__()
        self.enabled = enabled

    def state(self):
        return SimpleNamespace(enabled=self.enabled, reason="test")


class FakeBroker:
    def __init__(self):
        self.connected = False
        self.disconnected = False
        self.submit_calls = 0
        self.cancel_calls = 0
        self.replace_calls = 0

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.disconnected = True

    def get_account(self):
        return BrokerAccount("****acct", "CASH", "ACTIVE")

    def get_cash(self):
        return BrokerCash(Decimal("1000"), Decimal("0"), "USD")

    def get_positions(self):
        return ()

    def get_orders(self):
        return ()

    def submit_order(self, *args, **kwargs):
        self.submit_calls += 1
        raise AssertionError("observation mode must not submit")

    def cancel_order(self, *args, **kwargs):
        self.cancel_calls += 1
        raise AssertionError("observation mode must not cancel")

    def replace_order(self, *args, **kwargs):
        self.replace_calls += 1
        raise AssertionError("observation mode must not replace")


def configuration(tmp_path, *, live_enabled=False):
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
        live_trading_enabled=live_enabled,
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


def install_fakes(monkeypatch, tmp_path, *, stop_enabled=True, pending=()):
    config = configuration(tmp_path)
    auth = FakeClosable()
    journal = FakeClosable(pending=pending)
    market = FakeClosable()
    stop = FakeStop(enabled=stop_enabled)
    broker = FakeBroker()
    runtime = OperationalRuntimeComposition(
        configuration=config,
        authorization_registry=auth,
        execution_journal=journal,
        market_store=market,
        emergency_stop=stop,
        broker=broker,
    )

    monkeypatch.setattr(
        operational_main,
        "load_configuration",
        lambda: config,
    )
    monkeypatch.setattr(
        operational_main,
        "validate_environment",
        lambda value: None,
    )
    monkeypatch.setattr(
        operational_main,
        "ensure_parent_directories",
        lambda value: None,
    )
    monkeypatch.setattr(
        operational_main,
        "build_operational_runtime",
        lambda value: runtime,
    )
    monkeypatch.setattr(
        operational_main,
        "reconcile_startup",
        lambda *args: (),
    )
    monkeypatch.setattr(
        operational_main,
        "sleep_decimal",
        lambda seconds: None,
    )
    return broker, stop

def test_observation_mode_runs_bounded_without_mutations(monkeypatch, tmp_path, capsys):
    broker, _ = install_fakes(monkeypatch, tmp_path)

    assert operational_main.run_observation(
        max_cycles=2,
        interval_seconds=Decimal("0"),
    ) == 0

    output = capsys.readouterr().out
    assert "OBSERVATION MODE" in output
    assert "Order submission: DISABLED" in output
    assert "Observation cycle 2" in output
    assert broker.connected and broker.disconnected
    assert broker.submit_calls == broker.cancel_calls == broker.replace_calls == 0


def test_observation_mode_requires_active_emergency_stop(monkeypatch, tmp_path):
    broker, _ = install_fakes(monkeypatch, tmp_path, stop_enabled=False)

    with pytest.raises(RuntimeError, match="emergency stop"):
        operational_main.run_observation(max_cycles=1)

    assert not broker.connected


def test_observation_mode_rejects_live_trading_flag(
    monkeypatch,
    tmp_path,
):
    config = configuration(tmp_path, live_enabled=True)
    stop = FakeStop(enabled=True)
    runtime = OperationalRuntimeComposition(
        configuration=config,
        authorization_registry=FakeClosable(),
        execution_journal=FakeClosable(),
        market_store=FakeClosable(),
        emergency_stop=stop,
        broker=FakeBroker(),
    )

    monkeypatch.setattr(
        operational_main,
        "load_configuration",
        lambda: config,
    )
    monkeypatch.setattr(
        operational_main,
        "validate_environment",
        lambda value: None,
    )
    monkeypatch.setattr(
        operational_main,
        "ensure_parent_directories",
        lambda value: None,
    )
    monkeypatch.setattr(
        operational_main,
        "build_operational_runtime",
        lambda value: runtime,
    )

    with pytest.raises(
        RuntimeError,
        match="LIVE_TRADING_ENABLED=false",
    ):
        operational_main.run_observation(max_cycles=1)


def test_build_operational_runtime_uses_composition_root(
    monkeypatch,
    tmp_path,
):
    configured = configuration(tmp_path)
    composed = object()
    captured = {}

    def fake_create_operational_runtime_composition(
        *,
        configuration,
        clock,
        broker_factory,
    ):
        captured["configuration"] = configuration
        captured["clock"] = clock
        captured["broker_factory"] = broker_factory
        return composed

    monkeypatch.setattr(
        operational_main,
        "create_operational_runtime_composition",
        fake_create_operational_runtime_composition,
    )

    assert (
        operational_main.build_operational_runtime(configured)
        is composed
    )
    assert captured == {
        "configuration": configured,
        "clock": operational_main.utc_now,
        "broker_factory": operational_main.build_broker,
    }

def test_main_routes_run_arguments(monkeypatch):
    captured = {}

    def fake_run_observation(**kwargs):
        captured.update(kwargs)
        return 7

    monkeypatch.setattr(operational_main, "run_observation", fake_run_observation)
    monkeypatch.setattr(
        operational_main.sys,
        "argv",
        ["operational_main", "--run", "--max-cycles", "3", "--interval-seconds", "0.5"],
    )

    assert operational_main.main() == 7
    assert captured == {
        "max_cycles": 3,
        "interval_seconds": Decimal("0.5"),
    }

def test_check_startup_uses_operational_runtime_session(
    monkeypatch,
    tmp_path,
):
    install_fakes(monkeypatch, tmp_path)

    runtime = SimpleNamespace(
        authorization_registry=FakeClosable(),
        execution_journal=FakeClosable(),
        market_store=FakeClosable(),
        emergency_stop=FakeStop(enabled=True),
        broker=FakeBroker(),
    )
    events = []

    class FakeSession:
        def __init__(self, supplied_runtime):
            assert supplied_runtime is runtime
            events.append("created")

        def __enter__(self):
            events.append("entered")
            return self

        def connect(self):
            events.append("connected")
            runtime.broker.connect()

        def __exit__(self, exception_type, exception, traceback):
            events.append("exited")

    monkeypatch.setattr(
        operational_main,
        "build_operational_runtime",
        lambda configuration: runtime,
    )
    monkeypatch.setattr(
        operational_main,
        "OperationalRuntimeSession",
        FakeSession,
    )
    monkeypatch.setattr(
        operational_main,
        "reconcile_startup",
        lambda *args: (),
    )

    assert operational_main.check_startup() == 0
    assert events == [
        "created",
        "entered",
        "connected",
        "exited",
    ]
