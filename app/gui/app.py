from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.composition import create_desktop_composition
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
    )
    window.show()

    exit_code = application.exec()

    composition.close()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
