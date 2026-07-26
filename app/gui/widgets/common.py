from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class MetricCard(QFrame):
    def __init__(self, title: str, value: str = "--", note: str = "") -> None:
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        title_label = QLabel(title.upper())
        title_label.setObjectName("metricTitle")
        self._value = QLabel(value)
        self._value.setObjectName("metricValue")
        self._note = QLabel(note)
        self._note.setObjectName("muted")
        self._note.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(self._value)
        layout.addWidget(self._note)
        layout.addStretch()

    def set_value(self, value: str, note: str | None = None) -> None:
        self._value.setText(value)
        if note is not None:
            self._note.setText(note)


class StatusBadge(QLabel):
    def __init__(self, text: str = "UNKNOWN") -> None:
        super().__init__(text)
        self.setObjectName("statusBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_status(self, text: str, level: str = "neutral") -> None:
        self.setText(text.upper())
        self.setProperty("status", level)
        self.style().unpolish(self)
        self.style().polish(self)
