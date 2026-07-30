from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from app.gui.models import HealthDashboardSnapshot
from app.gui.widgets.common import StatusBadge


class HealthPanel(QWidget):
    """Render a prepared infrastructure health dashboard model."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._badge = StatusBadge("UNKNOWN")
        self._metrics = QGridLayout()
        self._incident = QLabel()
        self._incident.setWordWrap(True)
        layout.addWidget(self._badge)
        layout.addLayout(self._metrics)
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
            self._metrics.addWidget(name, row, column)
            self._metrics.addWidget(QLabel(value), row + 1, column)
        self._incident.setText(snapshot.incident)
