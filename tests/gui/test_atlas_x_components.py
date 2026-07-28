import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.components import (
    HealthIndicator,
    IconButton,
    MetricCard,
    PanelHeader,
    RuntimeBadge,
    SectionTitle,
    StatusCard,
    StatusPill,
)


APPLICATION = QApplication.instance() or QApplication([])


def test_reusable_components_construct_and_update() -> None:
    metric = MetricCard("Equity", "$1.00")
    status = StatusCard("Broker")
    runtime = RuntimeBadge()
    health = HealthIndicator("Market data")
    icon_button = IconButton("Start", style="primary")
    header = PanelHeader("Infrastructure")
    section = SectionTitle("Portfolio")
    pill = StatusPill()

    metric.set_value("$2.00", "Updated")
    status.set_status("Connected", "good")
    runtime.set_state("STARTING")
    health.set_health("Healthy", "good")
    pill.set_status("Running", "good")

    assert metric.value_label.text() == "$2.00"
    assert status.status_pill.property("status") == "good"
    assert runtime.property("status") == "warn"
    assert health.indicator.text() == "HEALTHY"
    assert icon_button.objectName() == "primaryButton"
    assert header.title.text() == "INFRASTRUCTURE"
    assert section.text() == "PORTFOLIO"
    assert pill.text() == "RUNNING"

