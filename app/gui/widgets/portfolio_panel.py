from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from app.gui.models import PortfolioDashboardSnapshot


class PortfolioPanel(QWidget):
    """Render a prepared portfolio dashboard snapshot."""

    def __init__(self) -> None:
        super().__init__()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._metrics = QGridLayout()
        self._highlights = QLabel()
        self._highlights.setWordWrap(True)
        self._layout.addLayout(self._metrics)
        self._layout.addWidget(self._highlights)

    def render(self, snapshot: PortfolioDashboardSnapshot) -> None:
        while self._metrics.count():
            item = self._metrics.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for index, (label, value) in enumerate(snapshot.metrics):
            name = QLabel(label)
            name.setObjectName("muted")
            column = index % 3
            row = (index // 3) * 2
            self._metrics.addWidget(name, row, column)
            self._metrics.addWidget(QLabel(value), row + 1, column)
        self._highlights.setText(
            "   •   ".join(
                f"{label}: {value}"
                for label, value in snapshot.highlights
            )
        )
