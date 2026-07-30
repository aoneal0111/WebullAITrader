from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)


class MetricCard(QFrame):
    def __init__(
        self,
        title: str,
        value: str = "--",
        note: str = "",
        *,
        emphasis: str = "standard",
    ) -> None:
        super().__init__()
        self.setObjectName("metricCard")
        self.setProperty("emphasis", emphasis)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(3)
        title_label = QLabel(title.upper())
        title_label.setObjectName("metricTitle")
        self._value = QLabel(value)
        self._value.setObjectName("metricValue")
        self._value.setProperty("emphasis", emphasis)
        self._note = QLabel(note)
        self._note.setObjectName("muted")
        self._note.setWordWrap(True)
        self._note.setVisible(bool(note))
        layout.addWidget(title_label)
        layout.addWidget(self._value)
        layout.addWidget(self._note)
        layout.addStretch()

    def set_value(self, value: str, note: str | None = None) -> None:
        self._value.setText(value)
        if note is not None:
            self._note.setText(note)
            self._note.setVisible(bool(note))

    def set_tone(self, tone: str) -> None:
        self._value.setProperty("tone", tone)
        self._value.style().unpolish(self._value)
        self._value.style().polish(self._value)


class StatusIndicator(QLabel):
    def __init__(self, text: str = "Unknown") -> None:
        super().__init__()
        self.setObjectName("statusIndicator")
        self.set_status(text, "neutral")

    def set_status(self, text: str, level: str = "neutral") -> None:
        self.setText(f"\u25cf  {text}")
        self.setProperty("status", level)
        self.style().unpolish(self)
        self.style().polish(self)


class StatusCard(QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("statusCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        title_row = QHBoxLayout()
        title_label = QLabel(title.upper())
        title_label.setObjectName("metricTitle")
        title_label.setWordWrap(True)
        self._indicator = StatusIndicator()
        title_row.addWidget(title_label)
        title_row.addStretch()
        title_row.addWidget(self._indicator)
        self._detail = QLabel("--")
        self._detail.setObjectName("muted")
        layout.addLayout(title_row)
        layout.addWidget(self._detail)

    def set_status(
        self,
        status: str,
        *,
        level: str = "neutral",
        detail: str = "--",
    ) -> None:
        self._indicator.set_status(status, level)
        self._detail.setText(detail)


class StatusBadge(QLabel):
    def __init__(self, text: str = "UNKNOWN") -> None:
        super().__init__(text)
        self.setObjectName("statusBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_status(self, text: str, level: str = "neutral") -> None:
        self.setText(text.upper())
        if text.upper() in {"UNKNOWN", "--"}:
            level = "neutral"
        self.setProperty("status", level)
        self.style().unpolish(self)
        self.style().polish(self)
