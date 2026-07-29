from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from app.gui.theme.spacing import Spacing


class SectionPanel(QFrame):
    def __init__(self, title: str, content: QWidget) -> None:
        super().__init__()
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Spacing.LG,
            14,
            Spacing.LG,
            Spacing.LG,
        )
        layout.setSpacing(Spacing.MD)
        heading = QLabel(title.upper())
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        layout.addWidget(content, 1)
