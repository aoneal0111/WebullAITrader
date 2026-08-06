from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.composition.desktop import create_desktop_composition
from app.gui.main_window import MainWindow


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("Webull AI Trader")
    application.setOrganizationName("Webull AI Trader")

    composition = create_desktop_composition()

    window = MainWindow(
        composition.bus,
        composition.state_store,
        composition.runtime_service,
        composition.trading_service,
        composition.order_command_factory,
        chart_market_data_service=composition.chart_market_data_service,
        chart_default_symbol=composition.chart_default_symbol,
    )
    window.show()

    try:
        return application.exec()
    finally:
        composition.close()


if __name__ == "__main__":
    raise SystemExit(main())
