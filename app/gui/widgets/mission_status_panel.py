from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QSizePolicy

from app.gui.models import MissionStatusSnapshot
from app.gui.widgets.common import StatusIndicator


class MissionStatusPanel(QFrame):
    """Readable mission-level status card backed by runtime facts."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("missionStatusCard")
        self.setMinimumHeight(118)
        self.setMinimumWidth(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(16, 12, 16, 12)
        self._layout.setHorizontalSpacing(20)
        self._layout.setVerticalSpacing(9)
        self._values: dict[str, StatusIndicator] = {}
        self._snapshot = MissionStatusSnapshot()
        self._columns = 0

    def render(self, snapshot: MissionStatusSnapshot) -> None:
        self._snapshot = snapshot
        self._rebuild()

    def resizeEvent(self, event) -> None:
        columns = 2 if event.size().width() >= 620 else 1
        if columns != self._columns:
            self._columns = columns
            self._rebuild()
        super().resizeEvent(event)

    def _rebuild(self) -> None:
        snapshot = self._snapshot
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._values.clear()

        columns = self._columns or (2 if self.width() >= 620 else 1)
        self._columns = columns
        for row_index, row in enumerate(snapshot.rows):
            label = QLabel(row.label)
            label.setObjectName("metricTitle")
            value = StatusIndicator()
            value.set_status(row.value, row.tone)
            value.setWordWrap(True)
            value.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            group = row_index % columns
            grid_row = row_index // columns
            base = group * 2
            self._layout.addWidget(label, grid_row, base)
            self._layout.addWidget(value, grid_row, base + 1)
            self._values[row.label] = value
        for group in range(columns):
            base = group * 2
            self._layout.setColumnMinimumWidth(base, 96)
            self._layout.setColumnStretch(base + 1, 1)
        rows = max(1, (len(snapshot.rows) + columns - 1) // columns)
        self.setMinimumHeight(30 + rows * 34)
        self.updateGeometry()


__all__ = ["MissionStatusPanel"]
