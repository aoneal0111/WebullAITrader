from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from app.gui.models import AtlasActivitySnapshot
from app.gui.widgets.common import StatusIndicator


class AtlasActivityPanel(QWidget):
    """Compact, read-only view of Atlas runtime and projection facts."""

    def __init__(self) -> None:
        super().__init__()
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(12)
        self._layout.setVerticalSpacing(3)
        self._rows: dict[str, StatusIndicator] = {}

    def render(self, snapshot: AtlasActivitySnapshot) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows.clear()

        for row_index, row in enumerate(snapshot.rows):
            name = QLabel(row.label)
            name.setObjectName("muted")
            value = StatusIndicator()
            value.set_status(row.value, row.tone)
            self._layout.addWidget(name, row_index, 0)
            self._layout.addWidget(value, row_index, 1)
            self._rows[row.label] = value
        self._layout.setColumnStretch(0, 1)


__all__ = ["AtlasActivityPanel"]
