import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from app.gui.models import HealthBadgeSnapshot, HealthCenterSnapshot
from app.gui.widgets.runtime_health_panel import RuntimeHealthPanel


APPLICATION = QApplication.instance() or QApplication([])


def test_panel_renders_snapshot_using_badges_without_controls() -> None:
    panel = RuntimeHealthPanel()
    initial = HealthCenterSnapshot.initial()
    snapshot = HealthCenterSnapshot(
        overall_health=HealthBadgeSnapshot(
            "Overall Health",
            "UNHEALTHY",
            "danger",
        ),
        runtime_state=HealthBadgeSnapshot(
            "Runtime State",
            "FAILED",
            "danger",
        ),
        broker_status=initial.broker_status,
        scanner_status=initial.scanner_status,
        market_data_status=initial.market_data_status,
        operations_bus_status=HealthBadgeSnapshot(
            "Operations Bus Status",
            "RECEIVING EVENTS",
            "good",
        ),
        current_cycle=HealthBadgeSnapshot(
            "Current Cycle",
            "4",
            "neutral",
        ),
        last_completed_cycle=HealthBadgeSnapshot(
            "Last Completed Cycle",
            "3",
            "neutral",
        ),
        last_update_time=HealthBadgeSnapshot(
            "Last Update Time",
            "15:00:00",
            "neutral",
        ),
        warnings=(
            HealthBadgeSnapshot("Warning", "Feed delayed", "warn"),
        ),
        errors=(
            HealthBadgeSnapshot("Error", "Runtime failed", "danger"),
        ),
    )

    panel.render(snapshot)

    assert panel.badges["Overall Health"].text() == "UNHEALTHY"
    assert panel.badges["Runtime State"].text() == "FAILED"
    assert panel.badges["Current Cycle"].text() == "4"
    assert panel.message_texts() == (
        "WARNING: FEED DELAYED",
        "ERROR: RUNTIME FAILED",
    )
    assert panel.findChildren(QPushButton) == []
    panel.deleteLater()
