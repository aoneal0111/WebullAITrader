from __future__ import annotations

import logging
import sys
from time import monotonic
from pathlib import Path

from dotenv import load_dotenv
from PySide6.QtWidgets import QApplication

from app.composition.desktop import create_desktop_composition
from app.composition.asset_modules import create_asset_modules
from app.configuration import load_configuration
from app.gui.main_window import MainWindow
from app.logging_config import configure_logging
from app.gui.runtime_ownership import acquire_runtime_ownership
from app.performance_diagnostics import ShutdownOrigin, ShutdownReason


def _record_application_quit(runtime_service) -> None:
    """Attribute Qt application exit without changing shutdown behavior."""
    try:
        runtime_service.note_shutdown_request(
            origin=ShutdownOrigin.APPLICATION_QUIT,
            reason=ShutdownReason.APPLICATION_EXIT,
            initiating_component="qt.application.about_to_quit",
            operator_initiated=True,
        )
    except Exception:
        pass


def configured_paper_persistence_path() -> Path:
    """Return the deterministic PAPER-only execution store path."""
    return Path(load_configuration().execution_database_path).with_name(
        "paper-execution.sqlite3"
    )


def _startup_phase(started: float, phase: str) -> None:
    """Bounded, payload-free wall duration since entering main.

    Diagnostic failures must never alter Atlas startup or PAPER ownership.
    """
    try:
        logging.getLogger("atlas.startup").info(
            "ATLAS_STARTUP_PHASE phase=%s elapsed_ms=%d",
            phase, max(0, round((monotonic() - started) * 1000)),
        )
    except Exception:
        pass


def main() -> int:
    started = monotonic()
    load_dotenv(override=False)
    configuration = load_configuration()
    ownership = None
    if configuration.environment.value == "PAPER":
        ownership = acquire_runtime_ownership(
            configured_paper_persistence_path().with_name("atlas-paper-runtime.lock")
        )
        if ownership is None:
            print("Atlas PAPER runtime is already owned by another process.", file=sys.stderr)
            return 2
    # Desktop runtime lifecycle events use the standard logging pipeline. This
    # is a no-op when an embedding process has already installed handlers.
    configure_logging()
    _startup_phase(started, "configuration_and_ownership_complete")
    try:
        application = QApplication(sys.argv)
        application.setApplicationName("Webull AI Trader")
        application.setOrganizationName("Webull AI Trader")
        _startup_phase(started, "qt_application_created")

        composition = create_desktop_composition(
            paper_persistence_path=configured_paper_persistence_path()
        )
        _startup_phase(started, "desktop_composition_created")
        application.aboutToQuit.connect(
            lambda: _record_application_quit(composition.runtime_service)
        )

        asset_modules = create_asset_modules(composition)
        _startup_phase(started, "asset_modules_created")
        window = MainWindow(
            composition.bus,
            composition.state_store,
            composition.runtime_service,
            composition.trading_service,
            composition.order_command_factory,
            chart_market_data_service=composition.chart_market_data_service,
            chart_default_symbol=composition.chart_default_symbol,
            warrior_forward_sidecar=composition.warrior_forward_sidecar,
            asset_modules=asset_modules,
        )
        _startup_phase(started, "main_window_constructed")
        window.show()
        _startup_phase(started, "window_show_requested")

        try:
            if composition.learning_collector is not None:
                composition.learning_collector.start()
            return application.exec()
        finally:
            if asset_modules.crypto_supervisor.close():
                asset_modules.crypto_supervisor.paper.close()
            composition.close()
    finally:
        if ownership is not None:
            ownership.release()


if __name__ == "__main__":
    raise SystemExit(main())
