from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow
from app.operations_core import ApplicationStateStore, OperationsBus
from app.services import RuntimeService, SimulatedPaperRuntimeDriver


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("Webull AI Trader")
    application.setOrganizationName("Webull AI Trader")

    bus = OperationsBus()
    state_store = ApplicationStateStore(bus)

    runtime_service = RuntimeService(
        bus,
        lambda: SimulatedPaperRuntimeDriver(
            interval_seconds=1.0,
            environment="PAPER",
            active_model="Promoted model",
        ),
    )

    window = MainWindow(
        bus,
        state_store,
        runtime_service,
    )
    window.show()

    exit_code = application.exec()

    runtime_service.close()
    state_store.close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
