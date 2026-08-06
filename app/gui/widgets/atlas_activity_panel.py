from __future__ import annotations

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from app.gui.models import AtlasActivitySnapshot
from app.gui.widgets.common import StatusIndicator


class AtlasActivityPanel(QWidget):
    """Compact, read-only view of Atlas runtime and projection facts."""

    def __init__(self) -> None:
        super().__init__()
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(8)
        self._layout.setVerticalSpacing(8)
        self._rows: dict[str, StatusIndicator] = {}
        self._cards: list[QFrame] = []

    def render(self, snapshot: AtlasActivitySnapshot) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows.clear()
        self._cards.clear()

        for row_index, row in enumerate(snapshot.rows):
            card = QFrame()
            card.setObjectName("activityMetric")
            card.setMinimumHeight(48)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.setSpacing(3)
            name = QLabel(row.label)
            name.setObjectName("metricTitle")
            value = StatusIndicator()
            value.set_status(row.value, row.tone)
            value.setWordWrap(True)
            card_layout.addWidget(name)
            card_layout.addWidget(value)
            grid_row, grid_column = divmod(row_index, 2)
            self._layout.addWidget(card, grid_row, grid_column)
            self._cards.append(card)
            self._rows[row.label] = value
        self._layout.setColumnStretch(0, 1)
        self._layout.setColumnStretch(1, 1)

    def resizeEvent(self, event) -> None:
        columns = 2
        for index, card in enumerate(self._cards):
            self._layout.addWidget(card, index // columns, index % columns)
        self._layout.invalidate()
        super().resizeEvent(event)


__all__ = ["AtlasActivityPanel"]
