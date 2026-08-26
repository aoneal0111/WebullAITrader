from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from PySide6.QtWidgets import QApplication

from app.composition.desktop import create_desktop_composition
from app.configuration import load_configuration
from app.gui.main_window import MainWindow
from app.logging_config import configure_logging


def configured_paper_persistence_path() -> Path:
    """Return the deterministic PAPER-only execution store path."""
    return Path(load_configuration().execution_database_path).with_name(
        "paper-execution.sqlite3"
    )


def main() -> int:
    load_dotenv(override=False)
    # Desktop runtime lifecycle events use the standard logging pipeline. This
    # is a no-op when an embedding process has already installed handlers.
    configure_logging()
    application = QApplication(sys.argv)
    application.setApplicationName("Webull AI Trader")
    application.setOrganizationName("Webull AI Trader")

    composition = create_desktop_composition(
        paper_persistence_path=configured_paper_persistence_path()
    )

    window = MainWindow(
        composition.bus,
        composition.state_store,
        composition.runtime_service,
        composition.trading_service,
        composition.order_command_factory,
        chart_market_data_service=composition.chart_market_data_service,
        chart_default_symbol=composition.chart_default_symbol,
        warrior_forward_sidecar=composition.warrior_forward_sidecar,
    )
    window.show()

    try:
        return application.exec()
    finally:
        composition.close()


if __name__ == "__main__":
    raise SystemExit(main())
