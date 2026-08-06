from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel

from app.gui.models import MissionStatusSnapshot
from app.gui.widgets.common import StatusIndicator


class MissionStatusPanel(QFrame):
    """Readable mission-level status card backed by runtime facts."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("missionStatusCard")
        self.setMinimumHeight(190)
        self.setMinimumWidth(320)
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(16, 12, 16, 12)
        self._layout.setHorizontalSpacing(20)
        self._layout.setVerticalSpacing(9)
        self._values: dict[str, StatusIndicator] = {}

    def render(self, snapshot: MissionStatusSnapshot) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._values.clear()

        for row_index, row in enumerate(snapshot.rows):
            label = QLabel(row.label)
            label.setObjectName("metricTitle")
            value = StatusIndicator()
            value.set_status(row.value, row.tone)
            value.setWordWrap(True)
            value.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            self._layout.addWidget(label, row_index, 0)
            self._layout.addWidget(value, row_index, 1)
            self._values[row.label] = value
        self._layout.setColumnMinimumWidth(0, 112)
        self._layout.setColumnStretch(1, 1)


__all__ = ["MissionStatusPanel"]
