"""Bounded offscreen Mission Control acceptance with no order commands."""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.broker_plugins import BrokerCapabilities, BrokerRuntime
from app.broker_protocol.models import BrokerAccount, BrokerCash
from app.composition.broker_account_projection import create_broker_account_publisher
from app.composition.desktop_broker_runtime import create_configured_desktop_broker_driver
from app.composition.runtime_projection_pipeline import create_runtime_projection_pipeline
from app.configuration import load_configuration
from app.gui.main_window import MainWindow
from app.operations_core import ApplicationStateStore, OperationsBus
from app.services import RuntimeService

from .audit_forward_capture import audit
from .desktop_sidecar import CompositeMarketEventObserver, WarriorDesktopSidecar
from .forward_models import PaperAccountContext
from .forward_store import ForwardCaptureStore


def run_acceptance(duration_seconds: int) -> dict[str, object]:
    if duration_seconds < 5 or duration_seconds > 120:
        raise ValueError("duration must be 5..120 seconds")
    application = QApplication.instance() or QApplication([])
    configuration = load_configuration()
    if not configuration.warrior_forward_paper_enabled:
        raise RuntimeError("WARRIOR_FORWARD_PAPER_ENABLED must be true")
    bus = OperationsBus()
    state_store = ApplicationStateStore(bus)
    projections = create_runtime_projection_pipeline(
        operations_bus=bus, account_id="warrior-acceptance",
    )
    sidecar = WarriorDesktopSidecar(
        enabled=True, storage_path=configuration.warrior_forward_capture_path,
        environment=configuration.environment.value,
        account_context_source=lambda: PaperAccountContext(
            equity=Decimal("25000"), buying_power=Decimal("25000"),
            allowed_symbols=frozenset(configuration.allowed_symbols),
        ),
    )
    observer = CompositeMarketEventObserver(None, sidecar)
    account_publisher = create_broker_account_publisher(bus)

    def driver_factory():
        return create_configured_desktop_broker_driver(
            event_sink=projections.sink,
            account_snapshot_sink=account_publisher,
            configuration_loader=lambda: configuration,
            broker_runtime_factory=lambda **kwargs: BrokerRuntime(
                provider="webull",
                capabilities=BrokerCapabilities(
                    provider="webull", version="acceptance-read-only",
                    supports_execution=True, supports_account_data=True,
                    supports_market_data=True, supports_streaming=True,
                    supports_stocks=True,
                ),
                execution=_ReadOnlyAcceptanceBroker(),
                market_data=kwargs["webull_market_data_factory"](
                    kwargs["configuration"]
                ),
            ),
            market_event_observer=observer,
        )

    runtime_service = RuntimeService(bus, driver_factory)
    before = ForwardCaptureStore(sidecar.storage_path).count()
    window = MainWindow(
        bus, state_store, runtime_service,
        warrior_forward_sidecar=sidecar,
    )
    window.show()
    heartbeats = []
    heartbeat = QTimer(window)
    heartbeat.setInterval(100)
    heartbeat.timeout.connect(lambda: heartbeats.append(monotonic()))
    heartbeat.start()
    statuses = []
    subscription = state_store.subscribe(
        lambda state: statuses.append((
            state.runtime.phase.value,
            state.health_projection.market_data_status,
            state.health_projection.scanner_status,
        ))
    )
    QTimer.singleShot(0, runtime_service.start)
    QTimer.singleShot(
        duration_seconds * 1000,
        lambda: runtime_service.stop("Bounded Warrior acceptance complete."),
    )

    def finish_when_stopped() -> None:
        if monotonic() - started > duration_seconds and not runtime_service.is_active:
            application.quit()

    started = monotonic()
    poll = QTimer(window)
    poll.setInterval(100)
    poll.timeout.connect(finish_when_stopped)
    poll.start()
    QTimer.singleShot((duration_seconds + 20) * 1000, application.quit)
    application.exec()
    state_store.unsubscribe(subscription)
    runtime_service.close(timeout_seconds=10)
    sidecar.stop()
    state_store.close()
    snapshot = sidecar.snapshot()
    evidence = audit(sidecar.storage_path)
    after = ForwardCaptureStore(sidecar.storage_path).count()
    gaps = [right - left for left, right in zip(heartbeats, heartbeats[1:])]
    return {
        "duration_seconds": duration_seconds,
        "runtime_started": any(item[0] == "RUNNING" for item in statuses),
        "market_data_connected": any(
            str(item[1]).upper() in {"CONNECTED", "READY", "SUBSCRIBED"}
            for item in statuses
        ),
        "scanner_running": any(str(item[2]).upper() in {"RUNNING", "WARMING"} for item in statuses),
        "warrior_health": snapshot.health.value,
        "focus_candidates": len(snapshot.items),
        "records_before": before, "records_after": after,
        "records_increased": after > before,
        "discovered": evidence["discovered"],
        "stocks_in_play": evidence["stocks_in_play"],
        "triggered_setups": evidence["triggered_setups"],
        "entry_ready": evidence["entry_ready"],
        "paper_entries": evidence["paper_entries"],
        "counterfactual_starts": evidence["counterfactual_starts"],
        "authoritative_spreads": evidence["authoritative_spreads"],
        "catalyst_states": evidence["catalyst_states"],
        "missing_data_counts": evidence["missing_data_counts"],
        "blocking_gates": evidence["blocking_gates"],
        "dropped_records": 0 if snapshot.metrics is None else snapshot.metrics.dropped_records,
        "queue_depth": 0 if snapshot.metrics is None else snapshot.metrics.queue_depth,
        "gui_refresh_frequency_hz": (
            "0" if snapshot.metrics is None else str(snapshot.metrics.gui_refresh_frequency_hz)
        ),
        "records_written": 0 if snapshot.metrics is None else snapshot.metrics.records_written,
        "average_write_latency_ms": (
            "0" if snapshot.metrics is None else str(snapshot.metrics.average_write_latency_ms)
        ),
        "maximum_write_latency_ms": (
            "0" if snapshot.metrics is None else str(snapshot.metrics.maximum_write_latency_ms)
        ),
        "duplicate_records": 0 if snapshot.metrics is None else snapshot.metrics.duplicate_records,
        "synchronous_fallback_records": (
            0 if snapshot.metrics is None else snapshot.metrics.synchronous_fallback_records
        ),
        "event_loop_heartbeats": len(heartbeats),
        "maximum_event_loop_gap_seconds": None if not gaps else max(gaps),
        "live_order_commands_invoked": 0,
    }


class _ReadOnlyAcceptanceBroker:
    """Deliberately has no submit/cancel/replace methods."""

    def connect(self) -> None: pass
    def disconnect(self) -> None: pass
    def get_account(self):
        return BrokerAccount("********", "CASH", "ACTIVE")
    def get_cash(self):
        return BrokerCash(Decimal("25000"), Decimal("0"), "USD",
                          buying_power=Decimal("25000"), equity=Decimal("25000"))
    def get_positions(self): return ()
    def get_orders(self): return ()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=20)
    arguments = parser.parse_args()
    print(json.dumps(run_acceptance(arguments.duration), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
