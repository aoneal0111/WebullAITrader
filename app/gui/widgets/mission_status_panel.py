from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from app.gui.models import MissionStatusSnapshot
from app.gui.widgets.common import StatusIndicator


class MissionStatusPanel(QWidget):
    """Compact mission-level summary backed by runtime health facts."""

    def __init__(self) -> None:
        super().__init__()
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(10)
        self._layout.setVerticalSpacing(2)
        self._values: dict[str, StatusIndicator] = {}

    def render(self, snapshot: MissionStatusSnapshot) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._values.clear()

        for column, row in enumerate(snapshot.rows):
            label = QLabel(row.label.upper())
            label.setObjectName("metricTitle")
            value = StatusIndicator()
            value.set_status(row.value, row.tone)
            self._layout.addWidget(label, 0, column)
            self._layout.addWidget(value, 1, column)
            self._layout.setColumnStretch(column, 1)
            self._values[row.label] = value


__all__ = ["MissionStatusPanel"]
