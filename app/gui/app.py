from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow
from app.operations_core import ApplicationStateStore, OperationsBus


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("Webull AI Trader")
    application.setOrganizationName("Webull AI Trader")

    bus = OperationsBus()
    state_store = ApplicationStateStore(bus)

    window = MainWindow(bus, state_store)
    window.show()

    exit_code = application.exec()

    state_store.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
