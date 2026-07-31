from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from app.broker_plugins import BrokerCapabilities, BrokerRuntime
from app.broker_protocol.models import BrokerAccount, BrokerCash
from app.composition.runtime_projection_pipeline import (
    create_runtime_projection_pipeline,
)
from app.configuration import OperationalConfiguration, TradingEnvironment
from app.gui.formatters import format_watchlist
from app.live_execution.account_polling import BrokerAccountSnapshot
from app.momentum_scanner import CatalystType, ScannerDecision, ScannerMetrics
from app.operations_core import ApplicationStateStore, OperationsBus
from app.realtime_scanner import ScannerSnapshot
from app.services import RuntimeService
from app.services.runtime_drivers import DesktopBrokerRuntimeDriver


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


class ReadOnlyBroker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def connect(self) -> None:
        self.calls.append("connect")

    def disconnect(self) -> None:
        self.calls.append("disconnect")

    def submit_order(self, order) -> None:
        raise AssertionError("scanner decisions must not submit orders")

    def cancel_order(self, order_id) -> None:
        raise AssertionError("scanner decisions must not cancel orders")

    def replace_order(self, order_id, order) -> None:
        raise AssertionError("scanner decisions must not replace orders")


class MarketDataBoundary:
    def set_lifecycle_sink(self, sink) -> None:
        self.lifecycle_sink = sink


class FakeScanner:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.observer = None

    def set_event_observer(self, observer) -> None:
        self.observer = observer

    def start(self, **kwargs):
        self.calls.append(("start", kwargs))
        return ("AUTO",)

    def run_available(self):
        self.calls.append("run_available")
        return SimpleNamespace(events_read=2, decisions_created=1)

    def snapshot(self):
        candidate = ScannerDecision(
            symbol="AUTO",
            qualified=True,
            score=91,
            metrics=ScannerMetrics(
                percentage_change=Decimal("14.5"),
                relative_volume=Decimal("7"),
                dollar_volume=Decimal("7000000"),
                spread_percent=Decimal("0.4"),
            ),
            passed_rules=(
                "price_range",
                "percentage_change",
                "relative_volume",
                "low_float",
                "news_catalyst",
                "tradable",
                "not_halted",
                "dollar_volume",
                "spread",
            ),
            failed_rules=(),
            timestamp=NOW,
            price=Decimal("5"),
            current_volume=Decimal("1400000"),
            catalyst=CatalystType.OTHER,
            catalyst_headline="Material company update",
        )
        return ScannerSnapshot(
            timestamp=NOW,
            active_symbols=("AUTO",),
            decisions=(candidate,),
            ranked_candidates=(candidate,),
            processed_events=2,
            ignored_events=0,
            reference_failures=(),
        )

    def stop(self) -> None:
        self.calls.append("stop")

    def disconnect(self) -> None:
        self.calls.append("disconnect")


def _configuration() -> OperationalConfiguration:
    return OperationalConfiguration(
        environment=TradingEnvironment.PAPER,
        broker_provider="webull",
        account_id="paper-account",
        api_key="key",
        api_secret="secret",
        api_base_url="https://api.sandbox.webull.com",
        stream_url="wss://data-api.sandbox.webull.com:8883/mqtt",
        authorization_database_path=Path("authorization.sqlite3"),
        execution_database_path=Path("execution.sqlite3"),
        market_event_database_path=Path("market.sqlite3"),
        emergency_stop_database_path=Path("stop.sqlite3"),
        log_level="INFO",
        health_port=8080,
        live_trading_enabled=False,
        max_order_notional=Decimal("10"),
        max_daily_notional=Decimal("50"),
        max_open_positions=1,
        max_open_orders=1,
        max_order_rate=5,
        max_quantity_per_symbol=Decimal("1"),
        allowed_symbols=(),
        blocked_symbols=(),
        maximum_market_data_age_seconds=5,
        reconciliation_interval_seconds=1,
        maximum_reconciliation_age_seconds=60,
        maximum_unresolved_mutations=0,
        market_data_streaming_enabled=True,
        market_data_symbols=(),
    )


def test_desktop_start_runs_scanner_projects_gui_and_stop_disconnects() -> None:
    bus = OperationsBus()
    store = ApplicationStateStore(bus)
    projections = create_runtime_projection_pipeline(
        operations_bus=bus,
        account_id="paper-account",
    )
    broker = ReadOnlyBroker()
    scanner = FakeScanner()
    account_polled = Event()
    snapshot_published = Event()
    event_types: list[str] = []

    def event_sink(event) -> None:
        event_types.append(event.event_type)
        projections.sink(event)
        if event.event_type == "scanner_snapshot_published":
            snapshot_published.set()

    def account_poller(broker_value, *, clock):
        account_polled.set()
        return BrokerAccountSnapshot(
            account=BrokerAccount(
                account_id_redacted="******ount",
                account_type="PAPER",
                status="ACTIVE",
            ),
            cash=BrokerCash(
                settled_cash=Decimal("1000"),
                unsettled_cash=Decimal("0"),
                currency="USD",
            ),
            positions=(),
            orders=(),
            observed_at=NOW,
        )

    runtime = BrokerRuntime(
        provider="webull",
        capabilities=BrokerCapabilities(
            provider="webull",
            version="test",
            supports_execution=True,
            supports_account_data=True,
            supports_market_data=True,
            supports_streaming=True,
        ),
        execution=broker,
        market_data=MarketDataBoundary(),
    )
    driver = DesktopBrokerRuntimeDriver(
        configuration=_configuration(),
        broker_runtime=runtime,
        event_sink=event_sink,
        account_snapshot_sink=lambda snapshot: None,
        account_poller=account_poller,
        scanner_coordinator=scanner,
        clock=lambda: NOW,
    )
    service = RuntimeService(bus, lambda: driver)

    try:
        assert service.start() is True
        assert snapshot_published.wait(2)
        assert account_polled.wait(2)
        assert service.stop() is True
        assert service.wait(3)

        start_call = scanner.calls[0]
        assert start_call[0] == "start"
        assert "channels" not in start_call[1]
        assert scanner.calls[-2:] == ["stop", "disconnect"]

        gui_snapshot = format_watchlist(
            store.snapshot().watchlist_projection
        )
        assert len(gui_snapshot.rows) == 1
        row = gui_snapshot.rows[0]
        assert row.symbol == "AUTO"
        assert row.rank == "1"
        assert row.score == "91"
        assert row.relative_volume == "7.00x"
        assert row.dollar_volume == "$7,000,000"
        assert row.catalyst.startswith("OTHER:")
        assert row.failed_rules == "--"
        assert row.freshness == "LIVE"
        assert row.session == "REGULAR"

        assert {
            "scanner_initialized",
            "universe_refreshed",
            "symbols_eligible",
            "market_data_connected",
            "channels_subscribed",
            "scanner_cycle",
            "events_consumed",
            "candidate_qualified",
            "scanner_snapshot_published",
        }.issubset(event_types)
        assert broker.calls == ["connect", "disconnect"]
    finally:
        service.close()
        store.close()
