from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import app.gui.main_window as main_window_module
import app.services.runtime_drivers.broker as broker_module
from app.gui.app import _record_application_quit
from app.gui.main_window import MainWindow
from app.operations_core import OperationsBus
from app.performance_diagnostics import (
    PerformanceDiagnostics,
    ShutdownOrigin,
    ShutdownReason,
)
from app.services import RuntimeService, SimulatedPaperRuntimeDriver
from app.services.runtime_drivers.broker import DesktopBrokerRuntimeDriver


def _service(diagnostics: PerformanceDiagnostics) -> RuntimeService:
    return RuntimeService(
        OperationsBus(),
        lambda: SimulatedPaperRuntimeDriver(interval_seconds=0.001),
        diagnostics=diagnostics,
    )


def test_runtime_operator_stop_records_first_origin_and_completion() -> None:
    diagnostics = PerformanceDiagnostics()
    service = _service(diagnostics)
    assert service.start()
    assert service.stop()
    assert service.wait(1.0)

    shutdown = diagnostics.shutdown_metrics()
    assert shutdown["shutdown_origin"] == "OPERATOR_STOP"
    assert shutdown["shutdown_reason"] == "OPERATOR_REQUESTED"
    assert shutdown["shutdown_operator_initiated"] is True
    assert shutdown["shutdown_cleanup_completed"] is True
    assert shutdown["shutdown_completed_at"] is not None
    assert [event["event_type"] for event in shutdown["events"]] == [
        "RUNTIME_STOP_REQUESTED",
        "RUNTIME_STOPPING",
        "RUNTIME_STOPPED",
    ]


def test_second_stop_and_close_cannot_overwrite_first_origin() -> None:
    diagnostics = PerformanceDiagnostics()
    service = _service(diagnostics)
    assert service.start()
    assert service.stop(
        "Window closed.",
        origin=ShutdownOrigin.GUI_CLOSE,
        shutdown_reason=ShutdownReason.GUI_WINDOW_CLOSED,
        initiating_component="gui.main_window.close_event",
    )
    service.stop(
        origin=ShutdownOrigin.OPERATOR_STOP,
        shutdown_reason=ShutdownReason.OPERATOR_REQUESTED,
    )
    assert service.close(timeout_seconds=1.0)
    shutdown = diagnostics.shutdown_metrics()
    assert shutdown["shutdown_origin"] == "GUI_CLOSE"
    assert shutdown["shutdown_reason"] == "GUI_WINDOW_CLOSED"
    assert shutdown["shutdown_component"] == "gui.main_window.close_event"


def test_gui_stop_and_close_supply_distinct_origins(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class Runtime:
        is_active = True

        def stop(self, reason="Operator requested shutdown.", **kwargs):
            calls.append({"reason": reason, **kwargs})
            return True

    button = SimpleNamespace(setText=lambda value: None, setEnabled=lambda value: None)
    status = SimpleNamespace(showMessage=lambda *args: None)
    fake = SimpleNamespace(
        _runtime_service=Runtime(),
        stop_button=button,
        start_button=button,
        statusBar=lambda: status,
        asset_navigation=SimpleNamespace(task=None),
        _close_requested=False,
        close=lambda: None,
    )
    MainWindow._stop_runtime(fake)
    assert calls[-1]["origin"] is ShutdownOrigin.OPERATOR_STOP

    monkeypatch.setattr(
        main_window_module,
        "QTimer",
        SimpleNamespace(singleShot=lambda *args: None),
    )
    event = SimpleNamespace(ignore=lambda: None, accept=lambda: None)
    MainWindow.closeEvent(fake, event)
    assert calls[-1]["origin"] is ShutdownOrigin.GUI_CLOSE
    assert calls[-1]["initiating_component"] == "gui.main_window.close_event"


def test_application_quit_is_distinct_and_diagnostic_failure_is_contained() -> None:
    captured = {}

    class Runtime:
        def note_shutdown_request(self, **kwargs):
            captured.update(kwargs)

    _record_application_quit(Runtime())
    assert captured["origin"] is ShutdownOrigin.APPLICATION_QUIT

    class BrokenRuntime:
        def note_shutdown_request(self, **kwargs):
            raise RuntimeError("diagnostic only")

    _record_application_quit(BrokenRuntime())


def test_runtime_shutdown_diagnostic_failure_does_not_block_stop() -> None:
    class BrokenDiagnostics(PerformanceDiagnostics):
        def record_shutdown_request(self, **kwargs):
            raise RuntimeError("diagnostic only")

        def record_shutdown_stopping(self, **kwargs):
            raise RuntimeError("diagnostic only")

        def record_shutdown_stopped(self, **kwargs):
            raise RuntimeError("diagnostic only")

    service = _service(BrokenDiagnostics())
    assert service.start()
    assert service.stop()
    assert service.wait(1.0)


def test_unexpected_runtime_driver_return_is_attributed_as_background_failure() -> None:
    class ReturningDriver:
        environment = "PAPER"
        active_model = "returning"
        cycles_completed = 0

        def run(self, *, stop_event, cycle_sink) -> None:
            return

    diagnostics = PerformanceDiagnostics()
    service = RuntimeService(
        OperationsBus(),
        ReturningDriver,
        diagnostics=diagnostics,
    )
    assert service.start()
    assert service.wait(1.0)
    shutdown = diagnostics.shutdown_metrics()
    assert shutdown["shutdown_origin"] == "BACKGROUND_TASK_FAILURE"
    assert shutdown["shutdown_reason"] == "BACKGROUND_TASK_TERMINATED"
    assert shutdown["shutdown_failure_present"] is True
    assert [event["event_type"] for event in shutdown["events"]] == [
        "RUNTIME_STOP_REQUESTED",
        "RUNTIME_STOPPING",
        "RUNTIME_STOPPED",
    ]


def _bare_consumer_driver(*, stop_consumer: bool, read_error: Exception | None):
    driver = object.__new__(DesktopBrokerRuntimeDriver)
    driver._market_data_consumer_launch_started = None
    driver._market_data_stop = Event()
    if stop_consumer:
        driver._market_data_stop.set()
    driver._shutdown_requested = False
    driver._scanner = None
    driver._market_data_consumer_attempted = False
    driver._market_data_failure_stage = None
    driver._market_data_failure_exception_class = None
    driver._market_event_observer = None
    driver._reconcile_temporal_orders = lambda: None
    driver._run_feed_watchdog = lambda: None
    driver._publish_terminal_market_data_failure = lambda exc: None
    driver._market_data_transport = lambda: SimpleNamespace(
        halt_callback_ingestion=lambda: None
    )

    class MarketData:
        def read_event(self):
            if read_error is not None:
                raise read_error
            return None

    driver._market_data = MarketData()
    return driver


def test_consumer_terminal_failure_records_failure_origin(monkeypatch) -> None:
    diagnostics = PerformanceDiagnostics()
    diagnostics.begin_runtime_session()
    monkeypatch.setattr(broker_module, "performance_diagnostics", diagnostics)
    driver = _bare_consumer_driver(
        stop_consumer=False,
        read_error=RuntimeError("not serialized"),
    )
    stop_event = Event()
    driver._receive_market_data(stop_event)

    shutdown = diagnostics.shutdown_metrics()
    assert stop_event.is_set()
    assert shutdown["shutdown_origin"] == "CONSUMER_FAILURE"
    assert shutdown["shutdown_failure_present"] is True
    assert shutdown["shutdown_exception_class"] == "RuntimeError"
    assert any(
        event["event_type"] == "UNEXPECTED_CONSUMER_TERMINATION"
        for event in shutdown["events"]
    )
    assert "not serialized" not in json.dumps(shutdown)
    diagnostics.record_shutdown_stopped(
        cleanup_completed=True,
        failure_present=False,
    )
    assert diagnostics.shutdown_metrics()["shutdown_failure_present"] is True


def test_consumer_unexpected_normal_exit_is_recorded_without_setting_stop(monkeypatch) -> None:
    diagnostics = PerformanceDiagnostics()
    diagnostics.begin_runtime_session()
    monkeypatch.setattr(broker_module, "performance_diagnostics", diagnostics)
    driver = _bare_consumer_driver(stop_consumer=True, read_error=None)
    stop_event = Event()
    driver._receive_market_data(stop_event)

    assert not stop_event.is_set()
    shutdown = diagnostics.shutdown_metrics()
    assert shutdown["shutdown_origin"] is None
    assert shutdown["events"][-1]["event_type"] == "UNEXPECTED_CONSUMER_TERMINATION"


def test_final_artifact_contains_original_shutdown_provenance(tmp_path: Path) -> None:
    diagnostics = PerformanceDiagnostics()
    artifact_path = tmp_path / "shutdown.json"
    diagnostics.start_run(artifact_path=artifact_path, flush_seconds=60.0)
    service = _service(diagnostics)
    assert service.start()
    assert service.stop(
        "Window closed.",
        origin=ShutdownOrigin.GUI_CLOSE,
        shutdown_reason=ShutdownReason.GUI_WINDOW_CLOSED,
        initiating_component="gui.main_window.close_event",
    )
    assert service.wait(1.0)
    diagnostics.finish_run()

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["shutdown_origin"] == "GUI_CLOSE"
    assert artifact["shutdown_reason"] == "GUI_WINDOW_CLOSED"
    assert artifact["shutdown_component"] == "gui.main_window.close_event"
    assert artifact["shutdown_cleanup_completed"] is True
    assert artifact["shutdown_completed_at"] is not None
    assert artifact["shutdown_operator_initiated"] is True
    assert artifact["shutdown_runtime_session_id"]
    assert artifact["shutdown_stop_event_already_set"] is False
    assert artifact["shutdown"]["shutdown_operator_initiated"] is True
    assert artifact["shutdown"]["shutdown_runtime_session_id"]
    assert len(artifact["shutdown"]["events"]) <= 32
