from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


class StatusPill(QLabel):
    LEVELS = frozenset({"neutral", "good", "warn", "danger", "info"})

    def __init__(
        self,
        text: str = "UNKNOWN",
        level: str = "neutral",
    ) -> None:
        super().__init__()
        self.setObjectName("statusPill")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_status(text, level)

    def set_status(self, text: str, level: str = "neutral") -> None:
        normalized = level if level in self.LEVELS else "neutral"
        self.setText(str(text).upper())
        self.setProperty("status", normalized)
        self.style().unpolish(self)
        self.style().polish(self)

