from __future__ import annotations

from PySide6.QtWidgets import QLabel


class SectionTitle(QLabel):
    def __init__(self, text: str) -> None:
        super().__init__(text.upper())
        self.setObjectName("sectionTitle")

