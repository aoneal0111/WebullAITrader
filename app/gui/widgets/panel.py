from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class SectionPanel(QFrame):
    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        action: QWidget | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        heading = QLabel(title.upper())
        heading.setObjectName("sectionTitle")
        header.addWidget(heading)
        header.addStretch()
        if action is not None:
            header.addWidget(action)
        layout.addLayout(header)
        layout.addWidget(content, 1)
