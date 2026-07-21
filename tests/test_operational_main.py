from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.configuration.models import OperationalConfiguration, TradingEnvironment
from app import operational_main


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
        return "account"

    def get_cash(self):
        return "cash"

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

    monkeypatch.setattr(operational_main, "load_configuration", lambda: config)
    monkeypatch.setattr(operational_main, "validate_environment", lambda value: None)
    monkeypatch.setattr(operational_main, "ensure_parent_directories", lambda value: None)
    monkeypatch.setattr(operational_main, "AuthorizationRegistry", lambda path: auth)
    monkeypatch.setattr(operational_main, "DurableExecutionJournal", lambda path: journal)
    monkeypatch.setattr(operational_main, "DurableMarketEventStore", lambda path: market)
    monkeypatch.setattr(operational_main, "EmergencyStopStore", lambda path, clock: stop)
    monkeypatch.setattr(operational_main, "build_broker", lambda value: broker)
    monkeypatch.setattr(operational_main, "reconcile_startup", lambda *args: ())
    monkeypatch.setattr(operational_main, "sleep_decimal", lambda seconds: None)
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


def test_observation_mode_rejects_live_trading_flag(monkeypatch, tmp_path):
    config = configuration(tmp_path, live_enabled=True)
    stop = FakeStop(enabled=True)
    monkeypatch.setattr(operational_main, "load_configuration", lambda: config)
    monkeypatch.setattr(operational_main, "validate_environment", lambda value: None)
    monkeypatch.setattr(operational_main, "ensure_parent_directories", lambda value: None)
    monkeypatch.setattr(operational_main, "AuthorizationRegistry", lambda path: FakeClosable())
    monkeypatch.setattr(operational_main, "DurableExecutionJournal", lambda path: FakeClosable())
    monkeypatch.setattr(operational_main, "DurableMarketEventStore", lambda path: FakeClosable())
    monkeypatch.setattr(operational_main, "EmergencyStopStore", lambda path, clock: stop)
    monkeypatch.setattr(operational_main, "build_broker", lambda value: FakeBroker())

    with pytest.raises(RuntimeError, match="LIVE_TRADING_ENABLED=false"):
        operational_main.run_observation(max_cycles=1)


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
