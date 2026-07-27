from __future__ import annotations

from decimal import Decimal

import pytest

from app.composition.operational_lifecycle import (
    OperationalRuntimeSession,
)
from app.composition.operational_runtime import (
    OperationalRuntimeComposition,
)
from app.configuration.models import (
    OperationalConfiguration,
    TradingEnvironment,
)


class RecordingDependency:
    def __init__(self, name, events):
        self.name = name
        self.events = events

    def close(self):
        self.events.append(f"{self.name}.close")


class RecordingBroker:
    def __init__(self, events):
        self.events = events

    def connect(self):
        self.events.append("broker.connect")

    def disconnect(self):
        self.events.append("broker.disconnect")


class JournalWithoutClose:
    pass


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


def runtime(tmp_path, events, *, execution_journal=None):
    return OperationalRuntimeComposition(
        configuration=configuration(tmp_path),
        authorization_registry=RecordingDependency(
            "authorization_registry",
            events,
        ),
        execution_journal=(
            execution_journal
            if execution_journal is not None
            else RecordingDependency("execution_journal", events)
        ),
        market_store=RecordingDependency("market_store", events),
        emergency_stop=RecordingDependency(
            "emergency_stop",
            events,
        ),
        broker=RecordingBroker(events),
    )


def test_operational_runtime_session_does_not_connect_on_enter(
    tmp_path,
):
    events = []
    session = OperationalRuntimeSession(runtime(tmp_path, events))

    with session as entered:
        assert entered is session
        assert not session.connected
        assert events == []

    assert events == [
        "market_store.close",
        "emergency_stop.close",
        "authorization_registry.close",
        "execution_journal.close",
    ]


def test_operational_runtime_session_connects_and_closes_in_order(
    tmp_path,
):
    events = []
    session = OperationalRuntimeSession(runtime(tmp_path, events))

    with session:
        session.connect()
        assert session.connected

    assert not session.connected
    assert events == [
        "broker.connect",
        "broker.disconnect",
        "market_store.close",
        "emergency_stop.close",
        "authorization_registry.close",
        "execution_journal.close",
    ]


def test_operational_runtime_session_closes_after_workflow_failure(
    tmp_path,
):
    events = []
    session = OperationalRuntimeSession(runtime(tmp_path, events))

    with pytest.raises(RuntimeError, match="workflow failed"):
        with session:
            session.connect()
            raise RuntimeError("workflow failed")

    assert events == [
        "broker.connect",
        "broker.disconnect",
        "market_store.close",
        "emergency_stop.close",
        "authorization_registry.close",
        "execution_journal.close",
    ]


def test_operational_runtime_session_accepts_journal_without_close(
    tmp_path,
):
    events = []
    session = OperationalRuntimeSession(
        runtime(
            tmp_path,
            events,
            execution_journal=JournalWithoutClose(),
        )
    )

    with session:
        session.connect()

    assert events == [
        "broker.connect",
        "broker.disconnect",
        "market_store.close",
        "emergency_stop.close",
        "authorization_registry.close",
    ]


def test_operational_runtime_session_requires_composed_runtime():
    with pytest.raises(
        TypeError,
        match="runtime must be OperationalRuntimeComposition",
    ):
        OperationalRuntimeSession(object())
