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
        composition.decision_projector,
        composition.runtime_health_projector,
        composition.timeline_projector,
        composition.trade_lifecycle_projector,
        composition.operator_workspace_projector,
        composition.replay_controller,
        composition.replay_projections,
        composition.recording_controller,
        composition.event_store_controller,
        composition.analytics_controller,
    )
    window.show()

    try:
        return application.exec()
    finally:
        composition.close()


if __name__ == "__main__":
    raise SystemExit(main())
