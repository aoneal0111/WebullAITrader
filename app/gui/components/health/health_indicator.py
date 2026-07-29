from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from app.gui.theme.spacing import Spacing

from app.gui.components.common import StatusPill


class HealthIndicator(QWidget):
    def __init__(
        self,
        label: str,
        status: str = "UNKNOWN",
        level: str = "neutral",
    ) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.SM)
        self.label = QLabel(label)
        self.label.setObjectName("metricTitle")
        self.indicator = StatusPill(status, level)
        layout.addWidget(self.label)
        layout.addStretch(1)
        layout.addWidget(self.indicator)

    def set_health(self, status: str, level: str) -> None:
        self.indicator.set_status(status, level)

