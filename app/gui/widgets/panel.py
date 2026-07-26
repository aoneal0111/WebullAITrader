from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class SectionPanel(QFrame):
    def __init__(self, title: str, content: QWidget) -> None:
        super().__init__()
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)
        heading = QLabel(title.upper())
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        layout.addWidget(content, 1)
