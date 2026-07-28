from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from .section_title import SectionTitle


class PanelHeader(QWidget):
    def __init__(
        self,
        title: str,
        subtitle: str = "",
        trailing: QWidget | None = None,
    ) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.title = SectionTitle(title)
        layout.addWidget(self.title)
        if subtitle:
            self.subtitle = QLabel(subtitle)
            self.subtitle.setObjectName("muted")
            layout.addWidget(self.subtitle)
        else:
            self.subtitle = None
        layout.addStretch(1)
        if trailing is not None:
            layout.addWidget(trailing)

