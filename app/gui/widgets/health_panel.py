from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from app.gui.design.tokens import Spacing
from app.gui.models import HealthDashboardSnapshot
from app.gui.widgets.common import StatusBadge


class HealthPanel(QWidget):
    """Render a prepared infrastructure health dashboard model."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("healthPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.SM, Spacing.SM, Spacing.SM, Spacing.SM)
        layout.setSpacing(Spacing.SM)
        self._badge = StatusBadge("UNKNOWN")
        metrics_widget = QWidget()
        metrics_widget.setObjectName("healthMetrics")
        self._metrics = QGridLayout(metrics_widget)
        self._metrics.setContentsMargins(0, 0, 0, 0)
        self._metrics.setHorizontalSpacing(Spacing.LG)
        self._metrics.setVerticalSpacing(Spacing.XS)
        scroll = QScrollArea()
        scroll.setObjectName("healthScrollArea")
        scroll.viewport().setObjectName("healthScrollViewport")
        scroll.setWidgetResizable(True)
        scroll.setWidget(metrics_widget)
        self._incident = QLabel()
        self._incident.setObjectName("muted")
        self._incident.setWordWrap(True)
        layout.addWidget(self._badge)
        layout.addWidget(scroll, 1)
        layout.addWidget(self._incident)

    def render(self, snapshot: HealthDashboardSnapshot) -> None:
        self._badge.set_status(
            snapshot.overall_status,
            snapshot.status_level,
        )
        while self._metrics.count():
            item = self._metrics.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for index, (label, value) in enumerate(snapshot.metrics):
            column = index % 3
            row = (index // 3) * 2
            name = QLabel(label)
            name.setObjectName("muted")
            value_label = QLabel(value)
            value_label.setObjectName("monoValue")
            self._metrics.addWidget(name, row, column)
            self._metrics.addWidget(value_label, row + 1, column)
        self._incident.setText(snapshot.incident)
